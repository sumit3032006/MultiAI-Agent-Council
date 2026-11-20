"""Multi-AI Agent Council - entry point."""
import os
import sys

import config


def main() -> int:
    config.load_env()
    try:
        from dashboard.ui import build_app, create_context
    except ImportError as e:
        print(f"Missing package: {e}. Run START.bat, or: pip install -r requirements.txt")
        return 1
    demo = build_app(create_context())
    port = int(os.environ.get("MAAC_PORT", "7860"))
    print(f"\nDashboard: http://127.0.0.1:{port}/?__theme=dark   (press Ctrl+C to stop)\n")
    demo.queue().launch(server_name="127.0.0.1", server_port=port, inbrowser=True, show_api=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
