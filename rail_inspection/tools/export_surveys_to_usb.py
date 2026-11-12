#!/usr/bin/env python3
"""Export locally saved survey files to a writable USB drive as single ZIP archives.

Every exported survey produces ONLY ONE password-protected .zip archive
(`<survey>.zip`) containing both:
  1. `<survey>.csv` (protected inside the ZIP archive)
  2. `<survey>.xlsx` (styled OOXML workbook, protected inside the ZIP archive; opens directly once extracted)
No loose .xlsx, .csv, or instruction text files remain on the pendrive.
"""

import argparse
import csv
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import zipfile
import zlib

DEFAULT_EXPORT_PASSWORD = os.environ.get("RAIL_EXPORT_PASSWORD", "Patil123")

PSEUDO_FILESYSTEMS = {
    "proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "cgroup", "cgroup2",
    "overlay", "squashfs", "nsfs", "pstore", "securityfs", "debugfs",
    "tracefs", "configfs", "fusectl", "mqueue", "autofs", "rpc_pipefs"
}

_XML_INVALID_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

# ==============================================================================
# Excel (.xlsx) Encryption Engine via msoffcrypto
# ==============================================================================

tools_dir = str(Path(__file__).resolve().parent)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

try:
    from msoffcrypto.format.ooxml import OOXMLFile
    import olefile
except Exception:
    try:
        from tools.msoffcrypto.format.ooxml import OOXMLFile
        import olefile
    except Exception:
        OOXMLFile = None
        olefile = None


def encrypt_excel_workbook(plain_xlsx_bytes, password):
    """Encrypt plain .xlsx bytes and verify container + HMAC integrity."""
    if OOXMLFile is None or olefile is None:
        raise RuntimeError("msoffcrypto/olefile not available for Excel encryption")

    buf_out = io.BytesIO()
    ooxml = OOXMLFile(io.BytesIO(plain_xlsx_bytes))
    ooxml.encrypt(password, buf_out)
    encrypted_bytes = buf_out.getvalue()

    # Verify OLE stream integrity before writing to pendrive
    ole = olefile.OleFileIO(io.BytesIO(encrypted_bytes))
    try:
        for stream_path in ole.listdir():
            expected_sz = ole.get_size(stream_path)
            actual_sz = len(ole.openstream(stream_path).read())
            if actual_sz != expected_sz:
                raise RuntimeError(
                    f"OLE stream {stream_path} truncated ({actual_sz} != {expected_sz})"
                )
    finally:
        ole.close()

    # Round-trip verify password and HMAC integrity
    check_file = OOXMLFile(io.BytesIO(encrypted_bytes))
    check_file.load_key(password=password, verify_password=True)
    dec_buf = io.BytesIO()
    check_file.decrypt(dec_buf, verify_integrity=True)
    if dec_buf.getvalue() != plain_xlsx_bytes:
        raise RuntimeError("Encrypted Excel round-trip mismatch")

    return encrypted_bytes


# ==============================================================================
# Excel (.xlsx) Document Generation
# ==============================================================================

def _excel_xml(value):
    if value is None:
        return ""
    cleaned = _XML_INVALID_RE.sub("", str(value))
    return (cleaned.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _excel_col(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


TRACK_GAUGE_MODES = {
    "straight": (-6.0, 6.0),
    "curve_ge_440": (-6.0, 15.0),
    "curve_lt_440": (-6.0, 20.0),
}


def _gauge_limits(limits_cfg=None):
    cfg = limits_cfg or {}
    mode = str(cfg.get("track_mode") or "straight").strip().lower()
    if mode not in TRACK_GAUGE_MODES:
        mode = "straight"
    nominal = float(cfg.get("nominal_gauge_mm") or 1676.0)
    min_tol, max_tol = TRACK_GAUGE_MODES[mode]
    return min_tol, max_tol, nominal + min_tol, nominal + max_tol


def _is_sensor_out_of_limit(header_name, value, limits_cfg=None):
    """Check whether a sensor value exceeds permissible display limits."""
    if value is None:
        return False
    val_str = str(value).strip()
    if not val_str:
        return False
    try:
        num = float(val_str)
    except (TypeError, ValueError):
        return False

    norm = re.sub(r"[^a-z]", "", str(header_name).lower())
    cfg = limits_cfg or {}
    cross_val = cfg.get("crosslevel_mm")
    cross_limit = 200.0 if cross_val is None else float(cross_val)

    # 1. Track Gauge: nominal 1676 mm, mode-dependent permissible tolerance
    if norm in ("gauge", "trackgauge", "trackgaugemm", "gaugemm"):
        min_tol, max_tol, min_abs, max_abs = _gauge_limits(cfg)
        if abs(num) > 1000.0:
            return num < min_abs or num > max_abs
        return num < min_tol or num > max_tol

    # 2. Cross Level: +/- 200 mm symmetric permissible limit
    if norm in ("crosslevel", "crosslevelmm", "cross", "crossover", "crosslvl", "absolutetilt"):
        return abs(num) > cross_limit

    # 3. Twist & Distance: no limits -> always keep white
    if norm in ("twist", "twistmmm", "twistmmperm", "cumulativetilt",
                "distance", "dist", "distm", "chainage"):
        return False

    return False


def _generate_unencrypted_xlsx_bytes(csv_file):
    """Generate in-memory .xlsx ZIP package with styling and limits."""
    try:
        cfg_path = Path(__file__).resolve().parents[1] / "rail_config.json"
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    except Exception:
        cfg = {}
    limits = cfg.get("permissible_limits", {})

    raw_text = csv_file.read_bytes().replace(b"\x00", b"").decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw_text, newline=""))
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    rows = list(reader)

    if not headers:
        raise ValueError(f"CSV file '{csv_file.name}' has no columns or data")

    header_cells = "".join(
        f'<c r="{_excel_col(index)}1" s="1" t="inlineStr"><is><t>{_excel_xml(name)}</t></is></c>'
        for index, name in enumerate(headers, 1))
    body = [f'<row r="1">{header_cells}</row>']
    for row_no, row in enumerate(rows, 2):
        cells = []
        for index, name in enumerate(headers, 1):
            value = row.get(name, "")
            red = _is_sensor_out_of_limit(name, value, limits)
            style = ' s="2"' if red else ''
            cells.append(f'<c r="{_excel_col(index)}{row_no}"{style} t="inlineStr"><is><t>{_excel_xml(value)}</t></is></c>')
        body.append(f'<row r="{row_no}">{"".join(cells)}</row>')

    max_col = _excel_col(len(headers))
    max_row = max(1, len(rows) + 1)
    dim_xml = f'<dimension ref="A1:{max_col}{max_row}"/>'

    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 f'{dim_xml}'
                 '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                 f'<sheetData>{"".join(body)}</sheetData></worksheet>')
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="3">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><color rgb="FF000000"/><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><color rgb="FFC00000"/><sz val="11"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="4">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFFFC7CE"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="2">'
        '<border/>'
        '<border>'
        '<left style="thin"><color rgb="FFD9D9D9"/></left>'
        '<right style="thin"><color rgb="FFD9D9D9"/></right>'
        '<top style="thin"><color rgb="FFD9D9D9"/></top>'
        '<bottom style="thin"><color rgb="FFD9D9D9"/></bottom>'
        '</border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="3">'
        '<xf fontId="0" fillId="0" borderId="1" xfId="0"/>'
        '<xf fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'
        '<xf fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'
        '</cellXfs>'
        '</styleSheet>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Survey" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        archive.writestr("xl/styles.xml", styles_xml)

    data = buf.getvalue()
    # Ensure EncryptedPackage stays strictly above the 4096-byte MS-CFB MiniStream
    # cutoff so small/short surveys are always stored in clean FAT sectors.
    if len(data) < 4608:
        pad_buf = io.BytesIO(data)
        with zipfile.ZipFile(pad_buf, "a", zipfile.ZIP_DEFLATED) as zpad:
            zpad.comment = b" " * (4608 - len(data) + 64)
        data = pad_buf.getvalue()
    return data


_CRC32_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0xEDB88320 if (_c & 1) else (_c >> 1)
    _CRC32_TABLE.append(_c)


def _zipcrypto_encrypt(raw_comp_bytes, crc32_val, password):
    """Encrypt payload with PKZIP 2.0 ZipCrypto stream cipher."""
    table = _CRC32_TABLE
    k0 = 0x12345678
    k1 = 0x23456789
    k2 = 0x34567890
    for b in str(password).encode("utf-8"):
        k0 = ((k0 >> 8) ^ table[(k0 ^ b) & 0xFF]) & 0xFFFFFFFF
        k1 = (((k1 + (k0 & 0xFF)) & 0xFFFFFFFF) * 134775813 + 1) & 0xFFFFFFFF
        k2 = ((k2 >> 8) ^ table[(k2 ^ ((k1 >> 24) & 0xFF)) & 0xFF]) & 0xFFFFFFFF

    header = os.urandom(11) + bytes([(crc32_val >> 24) & 0xFF])
    out = bytearray(12 + len(raw_comp_bytes))
    pos = 0
    for chunk in (header, raw_comp_bytes):
        for p in chunk:
            temp = (k2 | 2) & 0xFFFF
            key_byte = ((temp * (temp ^ 1)) >> 8) & 0xFF
            out[pos] = p ^ key_byte
            pos += 1
            k0 = ((k0 >> 8) ^ table[(k0 ^ p) & 0xFF]) & 0xFFFFFFFF
            k1 = (((k1 + (k0 & 0xFF)) & 0xFFFFFFFF) * 134775813 + 1) & 0xFFFFFFFF
            k2 = ((k2 >> 8) ^ table[(k2 ^ ((k1 >> 24) & 0xFF)) & 0xFF]) & 0xFFFFFFFF
    return bytes(out)


def _build_encrypted_zip_bytes(entries, password, mod_dt=None):
    """Build a standard password-protected ZIP archive containing multiple entries."""
    dt = mod_dt or datetime.now()
    dos_time = ((dt.hour & 0x1F) << 11) | ((dt.minute & 0x3F) << 5) | ((dt.second // 2) & 0x1F)
    dos_date = (((max(1980, dt.year) - 1980) & 0x7F) << 9) | ((dt.month & 0x0F) << 5) | (dt.day & 0x1F)
    flag = 0x0001 | 0x0800  # Encrypted + UTF-8 filename

    file_blobs = bytearray()
    central_records = []
    for arcname, raw_bytes in entries:
        name_bytes = str(arcname).encode("utf-8")
        crc = zlib.crc32(raw_bytes) & 0xFFFFFFFF
        comp_obj = zlib.compressobj(6, zlib.DEFLATED, -15)
        deflated = comp_obj.compress(raw_bytes) + comp_obj.flush()
        if len(deflated) < len(raw_bytes):
            method = 8
            payload = deflated
        else:
            method = 0
            payload = raw_bytes

        enc = _zipcrypto_encrypt(payload, crc, password)
        lfh_offset = len(file_blobs)
        lfh = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50, 20, flag, method, dos_time, dos_date,
            crc, len(enc), len(raw_bytes), len(name_bytes), 0,
        ) + name_bytes
        file_blobs.extend(lfh)
        file_blobs.extend(enc)

        cdh = struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50, 20, 20, flag, method, dos_time, dos_date,
            crc, len(enc), len(raw_bytes), len(name_bytes),
            0, 0, 0, 0, 0x20, lfh_offset,
        ) + name_bytes
        central_records.append(cdh)

    cd_data = b"".join(central_records)
    cd_offset = len(file_blobs)
    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50, 0, 0, len(entries), len(entries), len(cd_data), cd_offset, 0,
    )
    return bytes(file_blobs) + cd_data + eocd


def _zip_copy(csv_file, destination, password=DEFAULT_EXPORT_PASSWORD):
    """Create one password-protected .zip containing plain .csv and styled .xlsx."""
    plain_xlsx_bytes = _generate_unencrypted_xlsx_bytes(csv_file)
    raw_csv_bytes = csv_file.read_bytes()

    csv_arcname = f"{csv_file.stem}.csv"
    xlsx_arcname = f"{csv_file.stem}.xlsx"
    entries = [
        (csv_arcname, raw_csv_bytes),
        (xlsx_arcname, plain_xlsx_bytes),
    ]
    zip_bytes = _build_encrypted_zip_bytes(entries, password)

    pwd_bytes = str(password).encode("utf-8")
    # Self-check ZIP readability and password enforcement on both entries before writing to USB.
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zchk:
        names = zchk.namelist()
        expected = [csv_arcname, xlsx_arcname]
        if names != expected:
            raise ValueError(f"ZIP entry mismatch: {names} != {expected}")
        if zchk.read(csv_arcname, pwd=pwd_bytes) != raw_csv_bytes:
            raise ValueError("ZIP CSV round-trip verification failed")
        if zchk.read(xlsx_arcname, pwd=pwd_bytes) != plain_xlsx_bytes:
            raise ValueError("ZIP XLSX round-trip verification failed")

    output = destination / f"{csv_file.stem}.zip"
    with open(output, "wb") as f:
        f.write(zip_bytes)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    return output


def _excel_copy(csv_file, destination, password=DEFAULT_EXPORT_PASSWORD):
    """Create a password-protected .xlsx file with specified password."""
    output = destination / f"{csv_file.stem}.xlsx"
    plain_bytes = _generate_unencrypted_xlsx_bytes(csv_file)
    encrypted_bytes = encrypt_excel_workbook(plain_xlsx_bytes=plain_bytes, password=password)

    with open(output, "wb") as f:
        f.write(encrypted_bytes)
    return output


# ==============================================================================
# USB Mount Detection & Export Controller
# ==============================================================================

def _unescape_mount(value):
    return re.sub(r"\\\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def _is_removable(device):
    """Follow partition parents in sysfs until a removable USB disk is found."""
    block = Path("/sys/class/block") / Path(device).name
    while block.exists():
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
    mounts_path = Path("/proc/mounts")
    if not mounts_path.exists():
        return found
    for line in mounts_path.read_text().splitlines():
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


def _cleanup_legacy_files(directory):
    """Remove legacy zip, xlsx, csv, txt instruction, and duplicate report files."""
    if not directory.is_dir():
        return
    patterns = (
        "PASSWORD_INSTRUCTIONS.txt",
        "*.zip",
        "*.xlsx",
        "*.csv",
        "*.txt",
        "*_protected.xlsx",
        "*_COLORED_REPORT.xlsx",
        "*_RAW_DATA.csv",
    )
    for pattern in patterns:
        for stale in directory.glob(pattern):
            try:
                if stale.is_file():
                    stale.unlink()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="/home/debian/surveys")
    parser.add_argument("--file", dest="csv_files", action="append",
                        help="copy one selected completed CSV file (may be repeated)")
    parser.add_argument("--password", default=DEFAULT_EXPORT_PASSWORD,
                        help=f"password for exported ZIP and Excel files (default: {DEFAULT_EXPORT_PASSWORD})")
    parser.add_argument("--dest", type=Path,
                        help="optional destination directory instead of auto-detected USB mount")
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

    if args.dest is not None:
        survey_root = args.dest
        destination = args.dest
        destination.mkdir(parents=True, exist_ok=True)
    else:
        mounts = find_usb_mounts()
        if not mounts:
            print("ERROR: no writable mounted USB drive found. Insert a FAT32/exFAT USB drive and wait for it to mount.", file=sys.stderr)
            return 4
        if len(mounts) > 1:
            print("ERROR: more than one USB drive is mounted; leave only the target USB drive connected.", file=sys.stderr)
            return 5

        survey_root = mounts[0] / "RAIL_SURVEYS"
        destination = survey_root / datetime.now().strftime("%Y-%m-%d")
        destination.mkdir(parents=True, exist_ok=True)

    # Remove any legacy zip/xlsx/csv/txt/duplicate files in the survey root & subfolders
    _cleanup_legacy_files(survey_root)
    for subdir in survey_root.iterdir():
        if subdir.is_dir():
            _cleanup_legacy_files(subdir)
    _cleanup_legacy_files(destination)

    export_count = 0
    export_pwd = args.password or DEFAULT_EXPORT_PASSWORD

    for csv_file in csvs:
        # Save ONLY ONE FILE per survey: {csv_file.stem}.zip (containing .csv + protected .xlsx)
        try:
            _zip_copy(csv_file, destination, password=export_pwd)
        except Exception as e:
            print(f"ERROR: failed to create password-protected ZIP for {csv_file.name}: {e}", file=sys.stderr)
            return 6
        export_count += 1

    try:
        os.sync()
        subprocess.run(["sync"], timeout=10, check=False)
    except Exception:
        pass

    try:
        fd = os.open(destination, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass

    print(f"EXPORT OK: Exported {export_count} password-protected ZIP archive(s) (with .csv and .xlsx).")
    print(f"USB folder: {destination}")
    print("SAFE TO REMOVE USB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
