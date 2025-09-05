#!/usr/bin/env python3
"""Linux entry point for WebRTC Screen Share Hub.

This thin wrapper simply imports the cross-platform `webrtc_gui` module and
executes its `main()` function.  Keeping a dedicated launcher script allows
package managers or desktop shortcuts to reference an OS-specific filename
without duplicating the full 1 000-line implementation.
"""

from webrtc_gui import main

if __name__ == "__main__":
    main()
