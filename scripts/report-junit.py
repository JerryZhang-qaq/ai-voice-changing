"""Expose native Windows failures through GitHub check annotations."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def escape(value):
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main():
    root = ET.parse(Path(sys.argv[1])).getroot()
    failures = []
    for case in root.iter("testcase"):
        for failure in list(case):
            if failure.tag in {"failure", "error"}:
                message = f"{case.get('classname', '')}.{case.get('name', '')}\n{failure.text or failure.get('message', '')}"
                failures.append(message)
    for message in failures:
        print("::error title=Windows installer regression::" + escape(message[:12000]))
    print(f"Windows installer checks: {len(list(root.iter('testcase')))} cases; {len(failures)} failures")


if __name__ == "__main__":
    main()
