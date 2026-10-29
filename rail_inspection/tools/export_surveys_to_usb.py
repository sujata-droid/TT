#!/usr/bin/env python3
"""Copy locally saved CSV files to one writable, mounted removable USB drive."""
import argparse
import csv
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PSEUDO_FILESYSTEMS = {"proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "overlay", "squashfs", "nsfs", "pstore", "securityfs", "debugfs", "tracefs", "configfs", "fusectl", "mqueue", "autofs", "rpc_pipefs"}


def _excel_xml(value):
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _excel_col(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _excel_copy(csv_file, destination):
    """Create an .xlsx copy with static red cells above configured limits.

    This deliberately uses only Python's standard library so field deployment
    does not rely on a package download or internet connection.
    """
    try:
        cfg = json.loads((Path(__file__).resolve().parents[1] / "rail_config.json").read_text())
    except (OSError, ValueError, TypeError):
        cfg = {}
    limits = cfg.get("permissible_limits", {})
    limits = {"Gauge": limits.get("gauge_mm", 9.0),
              "Crosslevel": limits.get("crosslevel_mm"),
              "Twist": limits.get("twist_mm_per_m", 3.0)}
    with csv_file.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        rows = list(reader)
    output = destination / f"{csv_file.stem}.xlsx"
    header_cells = "".join(
        f'<c r="{_excel_col(index)}1" s="1" t="inlineStr"><is><t>{_excel_xml(name)}</t></is></c>'
        for index, name in enumerate(headers, 1))
    body = [f'<row r="1">{header_cells}</row>']
    for row_no, row in enumerate(rows, 2):
        cells = []
        for index, name in enumerate(headers, 1):
            value = row.get(name, "")
            try:
                red = limits.get(name) is not None and abs(float(value)) > float(limits[name])
            except (TypeError, ValueError):
                red = False
            style = ' s="2"' if red else ''
            cells.append(f'<c r="{_excel_col(index)}{row_no}"{style} t="inlineStr"><is><t>{_excel_xml(value)}</t></is></c>')
        body.append(f'<row r="{row_no}">{"".join(cells)}</row>')
    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                 f'<sheetData>{"".join(body)}</sheetData></worksheet>')
    styles_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                  '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFC62828"/><sz val="11"/><name val="Calibri"/></font></fonts>'
                  '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1565C0"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFEBEE"/><bgColor indexed="64"/></patternFill></fill></fills>'
                  '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
                  '<cellXfs count="3"><xf fontId="0" fillId="0" borderId="0" xfId="0"/><xf fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf fontId="2" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>')
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Survey" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        archive.writestr("xl/styles.xml", styles_xml)
    return output


def _unescape_mount(value):
    return re.sub(r"\\\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def _is_removable(device):
    """Follow partition parents in sysfs until a removable USB disk is found."""
    block = Path("/sys/class/block") / Path(device).name
    while block.exists():
        # Most USB thumb drives identify as USB-SCSI disks whose Linux
        # "removable" bit is 0, so also accept a block device below usbN.
        if any(part.startswith("usb") for part in block.resolve().parts):
            return True
        try:
            if (block / "removable").read_text().strip() == "1":
                return True
        except OSError:
            pass
        resolved = block.resolve()
        parent = resolved.parent
        if not (parent / "dev").exists():
            return False
        block = parent
    return False


def find_usb_mounts():
    found = []
    for line in Path("/proc/mounts").read_text().splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        device, mountpoint, fstype, options = fields[:4]
        mountpoint = Path(_unescape_mount(mountpoint))
        if (fstype in PSEUDO_FILESYSTEMS or "rw" not in options.split(",")
                or not device.startswith("/dev/") or not _is_removable(device)):
            continue
        if mountpoint.is_dir() and os.access(mountpoint, os.W_OK):
            found.append(mountpoint)
    return found


def _target_path(destination, source):
    target = destination / source.name
    index = 1
    while target.exists():
        if target.stat().st_size == source.stat().st_size:
            return None
        target = destination / f"{source.stem}_{index}{source.suffix}"
        index += 1
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="/home/debian/surveys")
    parser.add_argument("--file", dest="csv_files", action="append",
                        help="copy one selected completed CSV file (may be repeated)")
    args = parser.parse_args()
    source = Path(args.source)
    if args.csv_files:
        csvs = [Path(value) for value in args.csv_files]
        csvs = [value for value in csvs if value.is_file() and value.suffix.lower() == ".csv"]
    else:
        csvs = sorted(source.glob("*.csv")) if source.is_dir() else []
    if not csvs:
        print("ERROR: no CSV files available to export", file=sys.stderr)
        return 3
    mounts = find_usb_mounts()
    if not mounts:
        print("ERROR: no writable mounted USB drive found. Insert a FAT32/exFAT USB drive and wait for it to mount.", file=sys.stderr)
        return 4
    if len(mounts) > 1:
        print("ERROR: more than one USB drive is mounted; leave only the target USB drive connected.", file=sys.stderr)
        return 5
    destination = mounts[0] / "RAIL_SURVEYS" / datetime.now().strftime("%Y-%m-%d")
    destination.mkdir(parents=True, exist_ok=True)
    copied = skipped = excel = 0
    for csv_file in csvs:
        target = _target_path(destination, csv_file)
        if target is None:
            skipped += 1
        else:
            shutil.copy2(csv_file, target)
            copied += 1
        _excel_copy(csv_file, destination)
        excel += 1
    fd = os.open(destination, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    print(f"EXPORT OK: copied {copied}, already present {skipped}, Excel files {excel}")
    print(f"USB folder: {destination}")
    print("SAFE TO REMOVE USB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
