import os
import shutil
import subprocess

DEFAULT_TIMEOUT = 600      # packing/unpacking a full archive can be slow
CONVERT_TIMEOUT = 120      # single-file fcb <-> xml conversions

# ConvertBinaryObject's CLI args differ between builds/forks (--export, --mode export,
# drag-and-drop only...). Picking the wrong one just exits non-zero with no stderr,
# indistinguishable from a real failure, so we probe once and cache what worked.
EXPORT_STYLES = [
    ("--export",      lambda i, o: ["--export", i, o]),
    ("--mode export", lambda i, o: ["--mode", "export", i, o]),
    ("-e",            lambda i, o: ["-e", i, o]),
    ("positional",    lambda i, o: [i, o]),
    ("drag-and-drop", lambda i, o: [i]),
]
IMPORT_STYLES = [
    ("--import",      lambda i, o: ["--import", i, o]),
    ("--mode import", lambda i, o: ["--mode", "import", i, o]),
    ("-i",            lambda i, o: ["-i", i, o]),
    ("positional",    lambda i, o: [i, o]),
    ("drag-and-drop", lambda i, o: [i]),
]


class GibbedWrapper:
    def __init__(self, tools_dir):
        self.tools_dir = tools_dir

        if os.path.exists(os.path.join(tools_dir, "WD2Extract.exe")):
            self.unpack_exe = os.path.join(tools_dir, "WD2Extract.exe")
            self.pack_exe = os.path.join(tools_dir, "WD2Pack.exe")
            self.is_wd2_tools = True
        else:
            self.unpack_exe = os.path.join(tools_dir, "Gibbed.Disrupt.Unpack.exe")
            self.pack_exe = os.path.join(tools_dir, "Gibbed.Disrupt.Pack.exe")
            self.is_wd2_tools = False

        self.convert_exe = os.path.join(tools_dir, "Gibbed.Disrupt.ConvertBinaryObject.exe")

        self._export_style = None
        self._import_style = None
        self.last_probe = None

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    def tools_status(self):
        seven_zip = os.path.join(self.tools_dir, "7za.exe")
        projects = os.path.join(self.tools_dir, "projects")
        return {
            "toolset": "WD2 (WD2Extract/WD2Pack)" if self.is_wd2_tools else "Gibbed.Disrupt",
            "unpack": {"name": os.path.basename(self.unpack_exe), "ok": os.path.exists(self.unpack_exe)},
            "pack": {"name": os.path.basename(self.pack_exe), "ok": os.path.exists(self.pack_exe)},
            "convert": {"name": os.path.basename(self.convert_exe), "ok": os.path.exists(self.convert_exe)},
            "seven_zip": {"name": "7za.exe", "ok": os.path.exists(seven_zip)},
            # needs projects/ next to the exe for type defs or every conversion fails
            "projects": {"name": "projects/", "ok": os.path.isdir(projects)},
        }

    def is_ready(self):
        status = self.tools_status()
        return status["unpack"]["ok"] and status["pack"]["ok"]

    def can_convert(self):
        return os.path.exists(self.convert_exe)

    # ------------------------------------------------------------------
    # Process helper
    # ------------------------------------------------------------------
    def _run_cmd(self, cmd, timeout=DEFAULT_TIMEOUT, cwd=None):
        if not os.path.exists(cmd[0]):
            return False, "", (
                f"CRITICAL: Tool executable not found at '{cmd[0]}'. "
                "Place the WD2 Gibbed tools in your tools folder."
            )

        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                text=True,
                timeout=timeout,
                # projects/ is resolved relative to cwd, so default to the tools dir
                cwd=cwd or (self.tools_dir if os.path.isdir(self.tools_dir) else None),
            )
            if result.returncode != 0:
                detail = (result.stderr or "").strip() or (result.stdout or "").strip()
                if not detail:
                    shown = [os.path.basename(cmd[0])] + cmd[1:]
                    detail = f"exit code {result.returncode}, no output. Command: {' '.join(shown)}"
                return False, result.stdout, detail
            return True, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", (
                f"TIMEOUT: '{os.path.basename(cmd[0])}' did not finish within {timeout}s "
                "and was terminated."
            )
        except FileNotFoundError:
            return False, "", f"CRITICAL: Tool executable not found at '{cmd[0]}'."
        except Exception as exc:
            return False, "", f"ERROR: Failed to run '{os.path.basename(cmd[0])}': {exc}"

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    @staticmethod
    def companion_dir(xml_path):
        # export makes items_converted.xml AND items_converted/ - the folder has to
        # move with the xml or re-importing loses data
        return os.path.splitext(xml_path)[0]

    def _collect_dragdrop_output(self, src, dst, want_ext):
        # drag-and-drop mode dumps '<stem>_converted.<ext>' next to the input file
        stem = os.path.splitext(src)[0]
        produced = f"{stem}_converted{want_ext}"
        if not os.path.exists(produced):
            return False
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        shutil.move(produced, dst)
        produced_dir = f"{stem}_converted"
        if os.path.isdir(produced_dir):
            target_dir = self.companion_dir(dst)
            shutil.rmtree(target_dir, ignore_errors=True)
            shutil.move(produced_dir, target_dir)
        return True

    def _convert(self, src, dst, styles, cached_attr, want_ext):
        if not os.path.exists(self.convert_exe):
            return False, "", (f"CRITICAL: '{os.path.basename(self.convert_exe)}' not found "
                               f"in '{self.tools_dir}'.")
        if not os.path.exists(src):
            return False, "", f"Input file missing: {src}"

        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)

        cached = getattr(self, cached_attr)
        ordered = styles
        if cached:
            ordered = [s for s in styles if s[0] == cached] + [s for s in styles if s[0] != cached]

        attempts = []
        for name, build in ordered:
            # only wipe the companion folder on export - on import, companion_dir(dst)
            # can actually be the INPUT's folder (rt.xml/rt.fcb both -> rt/) and
            # deleting it would nuke the externals we're about to read
            stale = [dst]
            if want_ext == ".xml":
                stale.append(self.companion_dir(dst))
            for path in stale:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                elif os.path.isfile(path):
                    os.remove(path)

            cmd = [self.convert_exe] + build(src, dst)
            cwd = os.path.dirname(src) if name == "drag-and-drop" else None
            ok, out, err = self._run_cmd(cmd, timeout=CONVERT_TIMEOUT, cwd=cwd)

            if name == "drag-and-drop" and ok:
                ok = self._collect_dragdrop_output(src, dst, want_ext)
                if not ok:
                    err = "ran successfully but produced no '_converted' output"

            if ok and os.path.exists(dst) and os.path.getsize(dst) > 0:
                setattr(self, cached_attr, name)
                return True, out, ""

            attempts.append(f"[{name}] {(err or 'no output produced').strip()}")

        setattr(self, cached_attr, None)
        return False, "", "All argument styles failed:\n  " + "\n  ".join(attempts)

    def fcb_to_xml(self, fcb_path, output_xml_path):
        return self._convert(fcb_path, output_xml_path, EXPORT_STYLES, "_export_style", ".xml")

    def xml_to_fcb(self, xml_path, output_fcb_path):
        return self._convert(xml_path, output_fcb_path, IMPORT_STYLES, "_import_style", ".fcb")

    def probe_converter(self, sample_fcb, scratch_dir):
        # round-trips a real file so we can tell the user exactly what broke
        # instead of "conversion failed, good luck"
        os.makedirs(scratch_dir, exist_ok=True)
        report = {
            "sample": sample_fcb,
            "convert_exe": self.convert_exe,
            "convert_exists": os.path.exists(self.convert_exe),
            "projects_folder": os.path.isdir(os.path.join(self.tools_dir, "projects")),
            "export_ok": False, "export_style": None, "export_error": "",
            "xml_bytes": 0, "companion_files": 0,
            "import_ok": False, "import_style": None, "import_error": "",
            "roundtrip_bytes": 0, "original_bytes": 0,
        }
        if not report["convert_exists"]:
            report["export_error"] = "Converter executable not found."
            self.last_probe = report
            return report
        if not os.path.exists(sample_fcb):
            report["export_error"] = "No sample file available to test with."
            self.last_probe = report
            return report

        report["original_bytes"] = os.path.getsize(sample_fcb)
        xml_out = os.path.join(scratch_dir, "probe.xml")
        ok, _, err = self.fcb_to_xml(sample_fcb, xml_out)
        report["export_ok"] = ok
        report["export_style"] = self._export_style
        report["export_error"] = err
        if not ok:
            self.last_probe = report
            return report

        report["xml_bytes"] = os.path.getsize(xml_out)
        comp = self.companion_dir(xml_out)
        if os.path.isdir(comp):
            report["companion_files"] = sum(len(f) for _, _, f in os.walk(comp))

        fcb_out = os.path.join(scratch_dir, "probe_roundtrip.fcb")
        ok, _, err = self.xml_to_fcb(xml_out, fcb_out)
        report["import_ok"] = ok
        report["import_style"] = self._import_style
        report["import_error"] = err
        if ok:
            report["roundtrip_bytes"] = os.path.getsize(fcb_out)

        self.last_probe = report
        return report

    # ------------------------------------------------------------------
    # Archive operations
    # ------------------------------------------------------------------
    def unpack_archive(self, fat_path, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        return self._run_cmd([self.unpack_exe, fat_path, output_dir])

    def pack_archive(self, input_dir, output_fat_path):
        os.makedirs(os.path.dirname(output_fat_path), exist_ok=True)
        if self.is_wd2_tools:
            cmd = [self.pack_exe, input_dir, output_fat_path]
        else:
            cmd = [self.pack_exe, output_fat_path, input_dir]
        return self._run_cmd(cmd)
