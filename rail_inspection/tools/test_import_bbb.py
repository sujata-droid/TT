import sys
from pathlib import Path
tools_dir = "/home/debian/trolley/tools"
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

try:
    from msoffcrypto.format.ooxml import OOXMLFile
    print("SUCCESS: imported OOXMLFile on BBB!")
except Exception as e:
    import traceback
    traceback.print_exc()
