#!/usr/bin/env python3
"""macOS entry point for WebRTC Screen Share Hub.

This lightweight launcher imports the cross-platform `webrtc_gui` module and
starts its `main()` function.  Having a distinct filename lets packaging or
bundling tools (e.g. py2app, Homebrew formula) provide a mac-specific command
without duplicating the application code.
"""

from webrtc_gui import main

if __name__ == "__main__":
    main()
