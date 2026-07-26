import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.protocol_loader import get_deck

# backend/data/audio — consistent regardless of CWD
AUDIO_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "audio"
DEFAULT_VOICE = "kn-IN-SapnaNeural"


async def _edge_tts_synth(text: str, out_path: Path, voice: str) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice).save(str(out_path))


def synth_default(text: str, out_path: Path, voice: str) -> None:
    asyncio.run(_edge_tts_synth(text, out_path, voice))


def generate(
    deck: dict,
    out_dir: Path,
    force: bool = False,
    synth=None,
    voice: str = DEFAULT_VOICE,
) -> int:
    synth = synth or synth_default
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest: dict = {}
    if not force and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    made = 0
    for cid, c in deck.items():
        out_file = out_dir / f"{cid}.mp3"
        if out_file.exists() and not force:
            if cid not in manifest:
                manifest[cid] = {"file": out_file.name, "chars": len(c["kn"])}
            continue
        synth(c["kn"], out_file, voice)
        manifest[cid] = {
            "file": out_file.name,
            "chars": len(c["kn"]),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        made += 1

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    return made


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate Kannada IVR clips")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--engine", default="edge", choices=["edge", "bhashini"])
    ap.add_argument("--out", default=str(AUDIO_DIR))
    args = ap.parse_args()

    deck = get_deck()
    if args.engine == "bhashini":
        from app.config import settings

        if not settings.BHASHINI_API_KEY:
            print("bhashini engine requested but BHASHINI_API_KEY unset; "
                  "use --engine edge (default).", file=sys.stderr)
            sys.exit(2)
        # Bhashini pipeline wiring is a documented later enhancement;
        # edge-tts is the production-ready fallback today.
        print("bhashini engine not wired in this build.", file=sys.stderr)
        sys.exit(2)

    made = generate(deck, Path(args.out), force=args.force, voice=args.voice)
    print(f"generated={made} total_clips={len(deck)} out={args.out}")


if __name__ == "__main__":
    main()