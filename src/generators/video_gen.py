"""
Video Explainer Generator (OpenMontage & Edge-TTS Inspired)
Sản xuất video hướng dẫn người dùng chuyên nghiệp (MP4) 100% tự động:
1. Sinh giọng đọc thuyết minh AI bằng Microsoft Edge-TTS (tiếng Việt & tiếng Anh).
2. Khớp thời lượng chính xác giữa giọng đọc và ảnh chụp màn hình highlight.
3. Thiết kế khung hình chuẩn OpenMontage:
   - Lower-third banner hiển thị số bước + tiêu đề bước
   - Thanh tiến trình (Step Progress Bar) sinh động
   - Intro title card và Outro card mượt mà
4. Xuất video MP4 độ phân giải cao bằng MoviePy và FFmpeg.
"""
import os
import shutil
import asyncio
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import edge_tts
from moviepy import ImageClip, AudioFileClip, concatenate_videoclips
from rich.console import Console

from config.settings import settings
from src.ai.schemas import GuideBook, ProcessedStep

console = Console()


class VieNeuTTSProvider:
    """VieNeu-TTS Provider (pnnbao97/VieNeu-TTS: Vietnamese on-device neural TTS)"""
    _engine = None
    _init_attempted = False

    @classmethod
    def get_engine(cls):
        if not cls._init_attempted:
            cls._init_attempted = True
            try:
                from vieneu import Vieneu
                mode = getattr(settings, "vieneu_mode", "v3nano")
                console.print(f"[cyan]🦜 Đang khởi tạo mô hình VieNeu-TTS ({mode})...[/cyan]")
                cls._engine = Vieneu(mode=mode)
                console.print("[green]✨ VieNeu-TTS đã khởi tạo thành công![/green]")
            except Exception as e:
                console.print(f"[yellow]⚠️ Chưa thể nạp VieNeu-TTS cục bộ ({e}). Tự động dùng Edge-TTS dự phòng.[/yellow]")
                cls._engine = None
        return cls._engine

    @classmethod
    def generate_audio(cls, text: str, output_path: str, voice: Optional[str] = None) -> bool:
        engine = cls.get_engine()
        if not engine:
            return False
        try:
            out_p = Path(output_path)
            wav_tmp = out_p.with_suffix(".wav")

            # Chuẩn hóa voice name an toàn tuyệt đối, tránh lỗi KeyError
            chosen_voice = voice or getattr(settings, "vieneu_voice", "Ái Hân")
            available_voices = list(engine._preset_voices.keys()) if hasattr(engine, "_preset_voices") else []
            if available_voices and chosen_voice not in available_voices:
                chosen_voice = "Ái Hân" if "Ái Hân" in available_voices else available_voices[0]

            audio = engine.infer(text=text, voice=chosen_voice)
            engine.save(audio, str(wav_tmp))
            
            # Chuyển đổi WAV sang MP3 để tối ưu dung lượng và tương thích MoviePy
            if out_p.suffix.lower() == ".mp3":
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(wav_tmp), "-b:a", "192k", str(out_p)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True
                )
                try:
                    wav_tmp.unlink()
                except Exception:
                    pass
            elif str(wav_tmp) != str(out_p):
                wav_tmp.rename(out_p)
            return True
        except Exception as e:
            console.print(f"[yellow]⚠️ VieNeu-TTS gặp trục trặc ({e}), tự động chuyển sang Edge-TTS...[/yellow]")
            return False


class VideoGenerator:
    """Generates explainer video walkthroughs using Edge-TTS and MoviePy"""

    def __init__(self):
        self.output_dir = settings.output_dir / "videos"
        self.audio_dir = settings.output_dir / "audio"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    async def generate_speech_for_steps(self, guidebook: GuideBook, lang: str = "vi") -> List[str]:
        """Tạo file mp3 thuyết minh cho từng bước bằng VieNeu-TTS (cho tiếng Việt) hoặc Edge-TTS"""
        engine_vi = getattr(settings, "tts_engine_vi", "vieneu")
        engine_label = f"VieNeu-TTS ({getattr(settings, 'vieneu_mode', 'v3nano')})" if (lang == "vi" and engine_vi == "vieneu") else f"Edge-TTS"
        console.print(f"[cyan]🎙️ Đang tạo giọng đọc thuyết minh [{engine_label}] cho {len(guidebook.steps)} bước...[/cyan]")

        audio_files = []
        for step in guidebook.steps:
            script_text = (
                step.ai_content.voiceover_script.vi
                if lang == "vi"
                else step.ai_content.voiceover_script.en
            )
            filename = f"step_{step.step_number:02d}_{lang}.mp3"
            out_path = self.audio_dir / filename

            success = False
            # 1. Thử dùng VieNeu-TTS cho tiếng Việt nếu engine được cấu hình là vieneu
            if lang == "vi" and engine_vi == "vieneu":
                console.print(f"  [cyan]🦜 Đang tổng hợp giọng nói VieNeu-TTS bước {step.step_number}...[/cyan]")
                try:
                    # Chạy đồng bộ trong thread pool để không block asyncio loop
                    success = await asyncio.to_thread(
                        VieNeuTTSProvider.generate_audio,
                        script_text,
                        str(out_path)
                    )
                    if success:
                        console.print(f"  [green]✔️ Đã lưu audio VieNeu-TTS bước {step.step_number}[/green]")
                except Exception as e:
                    console.print(f"  [yellow]⚠️ VieNeu-TTS lỗi: {e}, chuyển sang Edge-TTS[/yellow]")

            # 2. Dự phòng sang Edge-TTS nếu VieNeu chưa khả dụng hoặc đối với tiếng Anh
            if not success:
                voice = settings.tts_voice_vi if lang == "vi" else settings.tts_voice_en
                console.print(f"  [cyan]🔊 Đang tổng hợp giọng nói Edge-TTS ({voice}) bước {step.step_number}...[/cyan]")
                try:
                    comm = edge_tts.Communicate(script_text, voice)
                    await comm.save(str(out_path))
                    success = True
                    console.print(f"  [green]✔️ Đã lưu audio Edge-TTS bước {step.step_number}[/green]")
                except Exception as e:
                    console.print(f"[red]❌ Lỗi tạo audio bước {step.step_number}: {e}[/red]")

            if success and Path(out_path).exists():
                audio_files.append(str(out_path))
                dur = 3.0
                try:
                    aclip = AudioFileClip(str(out_path))
                    dur = float(aclip.duration)
                    aclip.close()
                except Exception:
                    pass

                if lang == "vi":
                    step.audio_path_vi = str(out_path)
                    step.audio_duration_vi = dur
                else:
                    step.audio_path_en = str(out_path)
                    step.audio_duration_en = dur
            else:
                audio_files.append("")

        console.print(f"[green]✅ Đã hoàn tất toàn bộ {len(audio_files)} file âm thanh thuyết minh.[/green]")
        return audio_files

    def generate_video(self, guidebook: GuideBook, lang: str = "vi") -> str:
        """Dựng và kết xuất video MP4 hoàn chỉnh"""
        console.print(f"[bold magenta]🎬 [Video Generator][/bold magenta] Bắt đầu dựng video hướng dẫn ({lang.upper()})...")

        clips = []
        target_w, target_h = settings.video_width, settings.video_height
        total_steps = len(guidebook.steps)

        # 1. INTRO CARD (Tiêu đề đầu video - 2.5s)
        intro_frame = self._render_title_card(
            title=guidebook.title.vi if lang == "vi" else guidebook.title.en,
            subtitle="HƯỚNG DẪN THỰC HÀNH TỪ A ĐẾN Z" if lang == "vi" else "STEP-BY-STEP USER GUIDE",
            width=target_w,
            height=target_h
        )
        intro_clip = ImageClip(intro_frame).with_duration(2.5)
        clips.append(intro_clip)

        # 2. CÁC BƯỚC THAO TÁC (Step Clips kèm Voiceover Audio)
        for idx, step in enumerate(guidebook.steps):
            audio_path = step.audio_path_vi if lang == "vi" else step.audio_path_en
            step_title = step.ai_content.title.vi if lang == "vi" else step.ai_content.title.en
            img_path = step.annotated_screenshot_path

            # Chuẩn bị frame hình ảnh có overlay lower-third banner
            frame_np = self._compose_video_frame(
                image_path=img_path,
                step_num=step.step_number,
                total_steps=total_steps,
                step_title=step_title,
                target_w=target_w,
                target_h=target_h
            )

            if audio_path and Path(audio_path).exists():
                audio_clip = AudioFileClip(audio_path)
                # Thời lượng frame = thời lượng audio + khoảng nghỉ
                duration = audio_clip.duration + settings.audio_pause_after_step
                step_clip = ImageClip(frame_np).with_duration(duration).with_audio(audio_clip)
            else:
                # Nếu không có audio, mặc định 4 giây cho mỗi bước
                step_clip = ImageClip(frame_np).with_duration(4.0)

            clips.append(step_clip)

        # 3. OUTRO CARD (Kết thúc video - 2.5s)
        outro_frame = self._render_title_card(
            title="HOÀN THÀNH HƯỚNG DẪN!" if lang == "vi" else "GUIDE COMPLETE!",
            subtitle=f"Đã hoàn thành các bước trên {guidebook.target_url}" if lang == "vi" else f"All steps completed on {guidebook.target_url}",
            width=target_w,
            height=target_h,
            is_outro=True
        )
        outro_clip = ImageClip(outro_frame).with_duration(2.5)
        clips.append(outro_clip)

        # Nối tất cả các clip
        final_video = concatenate_videoclips(clips, method="compose")

        gid = getattr(guidebook, "guide_id", None) or datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"guide_{lang}_{gid}.mp4"
        out_video_path = self.output_dir / output_filename

        console.print(f"📦 Đang kết xuất video ra [bold]{out_video_path}[/bold]...")
        final_video.write_videofile(
            str(out_video_path),
            fps=settings.video_fps,
            codec="libx264",
            audio_codec="aac",
            threads=4,
            logger=None  # Tránh in rác terminal
        )

        # Lưu thêm bản latest để xem nhanh
        latest_path = self.output_dir / f"guide_{lang}.mp4"
        try:
            shutil.copyfile(str(out_video_path), str(latest_path))
        except Exception:
            pass

        # Giải phóng tài nguyên
        try:
            final_video.close()
            for c in clips:
                try:
                    c.close()
                except Exception:
                    pass
        except Exception:
            pass

        console.print(f"[bold green]🎉 Video {lang.upper()} đã hoàn thành:[/bold green] [underline]{out_video_path}[/underline]")
        return str(out_video_path)

    def _compose_video_frame(
        self,
        image_path: str,
        step_num: int,
        total_steps: int,
        step_title: str,
        target_w: int,
        target_h: int
    ) -> np.ndarray:
        """Tạo khung hình video có ảnh screenshot căn giữa và lower-third banner sắc nét"""
        # Tạo canvas nền đen hoặc xám đậm
        canvas = Image.new("RGB", (target_w, target_h), color=(15, 23, 42))

        # Đọc và resize ảnh screenshot tỷ lệ chuẩn
        if Path(image_path).exists():
            screenshot = Image.open(image_path).convert("RGB")
            # Resize fit trong khung hình (chừa chỗ cho banner dưới 120px)
            avail_h = target_h - 130
            avail_w = target_w - 40
            orig_w, orig_h = screenshot.size
            scale = min(avail_w / orig_w, avail_h / orig_h)
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)

            resized = screenshot.resize((new_w, new_h), Image.Resampling.LANCZOS)
            paste_x = (target_w - new_w) // 2
            paste_y = (avail_h - new_h) // 2 + 10
            canvas.paste(resized, (paste_x, paste_y))

        # Vẽ Lower-third banner phong cách OpenMontage
        draw = ImageDraw.Draw(canvas, "RGBA")
        banner_h = 110
        banner_y = target_h - banner_h

        # Nền banner mờ bán trong suốt
        draw.rectangle([(0, banner_y), (target_w, target_h)], fill=(10, 15, 30, 240))
        # Viền trên của banner
        draw.line([(0, banner_y), (target_w, banner_y)], fill=(51, 65, 85, 255), width=1)

        # Thanh tiến trình (Step Progress Bar) ở đỉnh banner
        progress_ratio = step_num / max(1, total_steps)
        progress_w = int(target_w * progress_ratio)
        draw.rectangle([(0, banner_y), (progress_w, banner_y + 4)], fill=(235, 35, 35, 255))

        # Badge số bước (hình chữ nhật bo tròn màu đỏ)
        badge_w, badge_h = 120, 44
        badge_x, badge_y = 50, banner_y + 32
        draw.rounded_rectangle(
            [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h)],
            radius=8,
            fill=(235, 35, 35, 255)
        )

        # Lấy font hệ thống
        font_badge = self._get_font(20, bold=True)
        font_title = self._get_font(26, bold=True)

        # Vẽ text badge: "BƯỚC 1"
        b_text = f"BƯỚC {step_num}"
        b_box = draw.textbbox((0, 0), b_text, font=font_badge)
        bw = b_box[2] - b_box[0]
        bh = b_box[3] - b_box[1]
        draw.text(
            (badge_x + (badge_w - bw) / 2, badge_y + (badge_h - bh) / 2 - 2),
            b_text,
            fill=(255, 255, 255),
            font=font_badge
        )

        # Vẽ Tiêu đề bước
        title_x = badge_x + badge_w + 30
        title_y = banner_y + 36
        draw.text((title_x, title_y), step_title[:65], fill=(255, 255, 255), font=font_title)

        return np.array(canvas)

    def _render_title_card(
        self,
        title: str,
        subtitle: str,
        width: int,
        height: int,
        is_outro: bool = False
    ) -> np.ndarray:
        """Tạo card mở đầu hoặc kết thúc video"""
        canvas = Image.new("RGB", (width, height), color=(15, 23, 42))  # Dark Slate
        draw = ImageDraw.Draw(canvas, "RGBA")

        # Viền đỏ trang trí ở giữa
        accent_y = height // 2 - 120
        draw.rectangle([(width // 2 - 80, accent_y), (width // 2 + 80, accent_y + 6)], fill=(235, 35, 35, 255))

        font_sub = self._get_font(22, bold=True)
        font_title = self._get_font(42, bold=True)

        # Subtitle
        sub_box = draw.textbbox((0, 0), subtitle, font=font_sub)
        sw = sub_box[2] - sub_box[0]
        draw.text((width // 2 - sw // 2, accent_y + 30), subtitle, fill=(235, 35, 35), font=font_sub)

        # Title chính
        t_box = draw.textbbox((0, 0), title[:60], font=font_title)
        tw = t_box[2] - t_box[0]
        draw.text((width // 2 - tw // 2, accent_y + 80), title[:60], fill=(255, 255, 255), font=font_title)

        return np.array(canvas)

    def _get_font(self, size: int, bold: bool = False):
        """Lấy font chữ từ hệ thống Windows hỗ trợ tiếng Việt Unicode"""
        paths = [
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/tahoma.ttf",
        ]
        for p in paths:
            if Path(p).exists():
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
        return ImageFont.load_default()
