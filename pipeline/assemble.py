"""Финальная сборка ролика через ffmpeg (viral-стиль, ресёрч 2026).

1) Каждый B-roll нормализуем в 1080x1920@30 + Ken Burns (медленный зум in/out поочерёдно —
   постоянное движение держит внимание, +eng). Короткие зацикливаем (-stream_loop).
2) Склейка concat-демуксером.
3) Вжигаем ASS-субтитры (libass) + progress-bar сверху; микс голос + музыка (ducking) +
   SFX-бэд (whoosh на склейках, ding на старте); loudnorm -14 LUFS; H.264/AAC +faststart.
"""
import pathlib

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core  # noqa: E402

BW, BH = int(core.W * 1.15), int(core.H * 1.15)   # предмасштаб ТОЛЬКО для картинок (запас под Ken Burns)
# видео/сток/градиент → сразу в финальные 1080x1920
_COVER = (f"scale={core.W}:{core.H}:force_original_aspect_ratio=increase,"
          f"crop={core.W}:{core.H},setsar=1,fps={core.FPS},format=yuv420p")
# картинка → больший канвас, затем zoompan вернёт к 1080x1920 с движением
_COVER_BIG = (f"scale={BW}:{BH}:force_original_aspect_ratio=increase,"
              f"crop={BW}:{BH},setsar=1,fps={core.FPS},format=yuv420p")
_KB_IN = (f"zoompan=z='min(1+0.0012*on,1.12)':d=1:x='iw/2-(iw/zoom/2)':"
          f"y='ih/2-(ih/zoom/2)':s={core.W}x{core.H}:fps={core.FPS}")
_KB_OUT = (f"zoompan=z='max(1.12-0.0012*on,1.0)':d=1:x='iw/2-(iw/zoom/2)':"
           f"y='ih/2-(ih/zoom/2)':s={core.W}x{core.H}:fps={core.FPS}")

SFX_DIR = core.ASSETS_DIR / "sfx"

# Кинематографичный цвет-грейдинг (один проход кодирования, без двойного сжатия) — +retention.
GRADE_STOCK = "unsharp=3:3:0.5,eq=contrast=1.04:brightness=-0.02:saturation=1.08:gamma=0.97"
GRADE_IMAGE = "unsharp=3:3:0.8,eq=contrast=1.05:brightness=-0.02:saturation=1.1:gamma=0.96"


def _normalize_clip(src: str, dur: float, out: pathlib.Path, idx: int, kind: str = "stock") -> pathlib.Path:
    is_img = kind == "image"   # ТОЛЬКО статичная картинка получает Ken Burns; видео уже движется
    # картинка → -loop 1; видео/сток/градиент → -stream_loop -1
    src_args = ["-loop", "1", "-i", src] if is_img else ["-stream_loop", "-1", "-i", src]
    if is_img:
        vf = _COVER_BIG + "," + (_KB_IN if idx % 2 == 0 else _KB_OUT)
    else:
        vf = _COVER   # видео/сток/градиент → точные 1080x1920, без zoompan
    core.run_retry([
        "ffmpeg", "-y", *src_args, "-t", f"{dur:.3f}",
        "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(core.FPS), "-vsync", "cfr", str(out),
    ])
    return out


def _ass_escape(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


# DISABLED: не вызывается — включить вернув cut_times в render (SFX-bed в прошлом лагал)
def _build_sfx_bed(cut_times: list[float], total: float, workdir: pathlib.Path) -> pathlib.Path | None:
    """Дорожка SFX: ding на 0.0 + whoosh на каждой склейке. None если файлов нет."""
    whoosh = SFX_DIR / "whoosh.wav"
    ding = SFX_DIR / "ding.wav"
    if not whoosh.exists():
        return None
    events = []  # (file, delay_sec)
    if ding.exists():
        events.append((str(ding), 0.0))
    for t in cut_times:
        if 0.2 < t < total - 0.2:
            events.append((str(whoosh), t))
    if not events:
        return None
    cmd = ["ffmpeg", "-y"]
    for f, _ in events:
        cmd += ["-i", f]
    parts, labels = [], []
    for i, (_, t) in enumerate(events):
        ms = int(t * 1000)
        parts.append(f"[{i}:a]adelay={ms}|{ms}[s{i}]")
        labels.append(f"[s{i}]")
    parts.append(f"{''.join(labels)}amix=inputs={len(events)}:normalize=0,"
                 f"apad,atrim=0:{total:.3f}[bed]")
    bed = workdir / "sfx_bed.wav"
    cmd += ["-filter_complex", ";".join(parts), "-map", "[bed]", str(bed)]
    try:
        core.run(cmd)
        return bed if bed.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _punch_expr(accents: list[dict] | None, total_dur: float | None = None, loop: bool = False) -> str:
    """zoompan-выражение для punch-in: короткий пульс зума (1.08x) в моменты акцентных слов.
    Привязка по времени `it` (in_time, сек). Пустая строка, если акцентов нет.
    При loop=True и заданном total_dur пропускаем акценты в последних ~0.5с (зона xfade-лупа,
    где пульс рассинхронизируется со склейкой конец→начало)."""
    if not accents:
        return ""
    z = "1"
    for a in accents[:40]:                          # верхний предел — не раздувать выражение
        t0 = float(a.get("t", 0)); t1 = t0 + float(a.get("dur", 0.2))
        if loop and total_dur is not None and t1 > total_dur - 0.5:
            continue                                # лупом обрезаемая зона — пульс не вжигаем
        z = f"if(between(it,{t0:.2f},{t1:.2f}),1.08,{z})"
    # центрированный зум, d=1 (по кадру), размер = финальный кадр
    return (f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={core.W}x{core.H}:fps={core.FPS}")


def _apply_loop(video_concat: pathlib.Path, first_clip: pathlib.Path,
                workdir: pathlib.Path) -> pathlib.Path | None:
    """Визуальный луп: плавный xfade конца ролика в его ОТКРЫВАЮЩИЙ кадр → стык конец→начало
    бесшовный, растёт replay-rate (replay=view). None при сбое (тогда берём обычную склейку)."""
    L = core.media_duration(video_concat)
    if L < 2.0:
        return None
    tail = workdir / "loop_tail.mp4"
    core.run(["ffmpeg", "-y", "-i", str(first_clip), "-t", "0.6", "-an",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
              "-pix_fmt", "yuv420p", "-r", str(core.FPS), str(tail)])
    out = workdir / "video_looped.mp4"
    off = max(0.1, L - 0.5)
    core.run_retry([
        "ffmpeg", "-y", "-i", str(video_concat), "-i", str(tail),
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=fade:duration=0.5:offset={off:.3f},format=yuv420p[v]",
        "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(core.FPS), str(out),
    ])
    return out if out.exists() and out.stat().st_size > 10_000 else None


def render(broll_list: list[dict], full_audio: pathlib.Path, total_dur: float,
           ass_path: pathlib.Path, out_mp4: pathlib.Path, music_path: str | None = None,
           workdir: pathlib.Path | None = None,
           accents: list[dict] | None = None, loop: bool = True) -> pathlib.Path:
    workdir = workdir or out_mp4.parent / "_work"
    workdir.mkdir(parents=True, exist_ok=True)

    # 1) нормализуем клипы (+ Ken Burns). Последнему даём +0.6с хвоста, чтобы видео
    #    гарантированно было НЕ короче аудио (иначе в конце фриз кадра) — финальный -t обрежет по аудио.
    n = len(broll_list)
    norm_paths = []
    for i, b in enumerate(broll_list):
        clip_dur = b["dur"] + (0.6 if i == n - 1 else 0.0)
        norm_paths.append(_normalize_clip(b["path"], clip_dur, workdir / f"clip_{i:02d}.mp4",
                                          i, kind=b.get("kind", "stock")))

    # 2) склейка
    concat_list = workdir / "concat.txt"
    concat_list.write_text("".join(f"file '{p}'\n" for p in norm_paths), encoding="utf-8")
    video_concat = workdir / "video_concat.mp4"
    core.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
              "-c", "copy", str(video_concat)])

    # 2b) визуальный луп: бесшовный стык конец→начало (replay=view). Опционально, с фолбэком.
    video_for_mux = video_concat
    if loop and norm_paths:
        try:
            looped = _apply_loop(video_concat, norm_paths[0], workdir)
            if looped:
                video_for_mux = looped
        except Exception as e:  # noqa: BLE001 — луп не критичен, рендер не роняем
            core.log_error("assemble.loop", e)

    # 3) SFX-бэд — whoosh на жёстком склейке звучал как лаг, ОТКЛЮЧЕН.
    #    (вернём, когда будут настоящие визуальные переходы под звук.)
    sfx_bed = None

    # 4) видео-фильтр: [punch-in зум на акцентах] → цвет-грейд → субтитры → progress-bar (жёлтая полоса).
    #    punch-in идёт ПЕРВЫМ (двигает фон), grade тоном поверх, субтитры рисуются ПОВЕРХ (стабильны).
    #    punch только при ≥2 акцентах (при 0-1 лишний полнокадровый zoompan-проход не оправдан).
    punch = _punch_expr(accents, total_dur=total_dur, loop=loop) if (accents and len(accents) >= 2) else ""
    has_img = any(b.get("kind") == "image" for b in broll_list)
    grade = GRADE_IMAGE if has_img else GRADE_STOCK
    vf = ((punch + ",") if punch else "") + grade + "," + (
        f"ass={_ass_escape(str(ass_path))},"
        f"drawbox=x=0:y=0:w='iw*t/{total_dur:.3f}':h=10:color=0xFFD93D@0.85:t=fill")

    # 5) аудио-микс: голос + (музыка с sidechain-ducking) + (SFX)
    cmd = ["ffmpeg", "-y", "-i", str(video_for_mux), "-i", str(full_audio)]
    # полировка голоса: деэссер (убрать «ш/с») + мягкая компрессия (ровная громкость) — чистый ffmpeg.
    # Если есть музыка — раздваиваем голос (asplit): одна копия в микс, вторая = ключ для sidechain.
    idx = 2
    a_inputs, mix = [], ["[voice]"]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", str(music_path)]
        a_inputs.append("[1:a]deesser=i=0.4,acompressor=threshold=-18dB:ratio=3:attack=10:release=120,"
                        "volume=1.0,asplit=2[voice][vkey]")
        # музыка громче базовых 12%, но sidechaincompress РЕЗКО утихает её под голос и
        # плавно возвращает в паузах — профессиональный «дакинг», голос всегда разборчив.
        a_inputs.append(f"[{idx}:a]volume=0.22[mus0]")
        a_inputs.append("[mus0][vkey]sidechaincompress=threshold=0.025:ratio=12:attack=15:"
                        "release=320:makeup=1[mus]")
        mix.append("[mus]"); idx += 1
    else:
        a_inputs.append("[1:a]deesser=i=0.4,acompressor=threshold=-18dB:ratio=3:attack=10:release=120,volume=1.0[voice]")
    if sfx_bed:
        cmd += ["-i", str(sfx_bed)]
        a_inputs.append(f"[{idx}:a]volume=0.9[sfx]")
        mix.append("[sfx]"); idx += 1
    amix = (f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0:dropout_transition=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
    filt = f"[0:v]{vf}[v];" + ";".join(a_inputs) + ";" + amix

    cmd += [
        "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
        "-t", f"{total_dur:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-maxrate", "6M", "-bufsize", "12M", "-pix_fmt", "yuv420p", "-r", str(core.FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out_mp4),
    ]
    core.run_retry(cmd, timeout=900)
    return out_mp4
