from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if not getattr(sys,'frozen',False):sys.path[:0]=[str(ROOT/'src'),str(ROOT/'studio'),str(ROOT/'desktop')]
from mouse_fusion_desktop.studio_main import main
from mouse_fusion_desktop.runtime import launch_gui
if __name__=='__main__':raise SystemExit(main() if '--service' in sys.argv else launch_gui(main))
