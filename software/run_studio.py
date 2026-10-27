"""Launch the repository Studio with its existing SDK, no installation required."""
from pathlib import Path
import sys

SOFTWARE=Path(__file__).resolve().parent
sys.path[:0]=[str(SOFTWARE/'src'),str(SOFTWARE/'studio')]


def main():
    import argparse
    import tkinter as tk
    from mouse_fusion_studio.app import StudioApp
    parser=argparse.ArgumentParser(description='Mouse Fusion Studio — local editor and explicit J4 connection')
    parser.add_argument('--project',type=Path)
    parser.add_argument('--endpoint',type=Path,help='Existing local service endpoint; live backend requires explicit opt-in')
    parser.add_argument('--allow-live-device',action='store_true')
    parser.add_argument('--live-com',help='Exact COM port owned by the existing service; no enumeration')
    parser.add_argument('--board-id',help='Exact 16-digit Flash board ID')
    parser.add_argument('--c-simulator',type=Path,help='Existing offline C simulator executable')
    parser.add_argument('--preset-c-simulator',type=Path,help='Offline production-C preset execution simulator')
    args=parser.parse_args()
    if args.live_com:
        import re
        if not args.allow_live_device or not re.fullmatch(r'COM[1-9][0-9]*',args.live_com,re.I) or not re.fullmatch(r'[0-9a-fA-F]{16}',args.board_id or ''):
            parser.error('--live-com requires exact COM, --board-id and --allow-live-device')
        if args.endpoint or args.c_simulator or args.preset_c_simulator:parser.error('Live COM cannot be mixed with other backends')
    if not args.live_com and not args.endpoint and not args.c_simulator and not args.preset_c_simulator:
        for build in ('build_eight_template_runtime_20261013_04','build_studio_v2_20261013_01','build_mode_control_20261013_01','build_bypass_20261013_01','build_run_policy_20261012_01'):
            candidate=SOFTWARE.parent/'firmware/mouse_passthrough/tests'/build/'j4_preset_simulator.exe'
            if candidate.is_file():args.preset_c_simulator=candidate;break
    root=tk.Tk()
    width=min(1480,root.winfo_screenwidth()-60);height=min(940,root.winfo_screenheight()-100)
    root.geometry(f'{width}x{height}');root.minsize(1000,640)
    app=StudioApp(root,endpoint=args.endpoint,c_simulator=args.c_simulator,preset_c_simulator=args.preset_c_simulator,
                  allow_live_device=args.allow_live_device,live_com=args.live_com,board_id=args.board_id)
    if args.project:
        try: app.open_from(args.project)
        except (ValueError,OSError) as error: app.report_error(error)
    def close():
        if app.confirm_unsaved(): app.dispose(); root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    if args.allow_live_device and (args.live_com or args.endpoint):
        app.show_device()
        # Explicit live launch performs identity/config queries only; no motion or RAM apply.
        root.after(100,lambda:app.request_api('connect'))
    root.mainloop()


if __name__=='__main__': main()
