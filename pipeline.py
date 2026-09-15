import os
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from gibbed import GibbedWrapper
from merger import XMLTreeMerger

# .obj in generated/databases is the same FCB format as .lib (ConvertBinaryObject
# handles both). forgot this once and onlineactivitysettings_*.obj silently fell
# back to last-wins for a while before I caught it.
MERGEABLE_EXTENSIONS = {".fcb", ".lib", ".obj", ".xml"}
# subset that's actually binary and needs FCB<->XML conversion; the rest is already XML
FCB_EXTENSIONS = {".fcb", ".lib", ".obj"}

# "absent from a mod's file = deleted" stops being a safe assumption once too
# much of vanilla is missing -- a partially rebuilt file looks identical to one
# that nuked most of the database. Past this ratio we keep the edits/additions
# but skip the removals, since applying them can break other mods' references.
DELETION_SAFETY_RATIO = 0.25
# ratio check only kicks in above this many entries -- removing 2 of 5 is a normal edit
DELETION_SAFETY_MIN_ENTRIES = 50
UNPACK_EXTENSIONS = {".fat", ".dat"}

# Vanilla archives, highest priority first. resolve_base_file returns the first
# match, so patch2 beats common when a file's in both -- that's the copy the
# game actually loads and what a mod was built against. patch3 is NOT listed
# on purpose: it's our own output, using it as a merge baseline would diff
# mods against an already-modded state.
# (shadersobj/sound/videos skipped too, nothing mergeable in there anyway)
BASELINE_ARCHIVES = [
    "patch2_english.fat",
    "patch2.fat",
    "patch_english.fat",
    "patch.fat",
    "installpackage_english.fat",
    "installpackage.fat",
    "common.fat",
]


class ModPipeline:
    """
    Reminder to self: patch3.fat/.dat aren't vanilla files, we generate them.
    So there's nothing to back up, "restoring vanilla" just means deleting
    them, and they can never be used as a merge baseline. The real baseline
    (patch2 -> patch -> common) gets extracted separately into workspace/base.
    """

    def __init__(self, workspace_dir, tools_dir, log_callback=None):
        self.workspace = os.path.abspath(workspace_dir)
        self.base_dir = os.path.join(self.workspace, "base")
        self.cache_dir = os.path.join(self.workspace, "mods_cache")
        self.temp_dir = os.path.join(self.workspace, "temp")
        self.staging_dir = os.path.join(self.workspace, "staging")
        self.output_dir = os.path.join(self.workspace, "output")

        self.gibbed = GibbedWrapper(tools_dir)
        self._roundtrip_cache = {}
        self.merger = XMLTreeMerger()
        self.log = log_callback or (lambda msg: print(f"[Pipeline] {msg}"))

    def init_workspace(self):
        for d in [self.base_dir, self.cache_dir, self.temp_dir, self.staging_dir, self.output_dir]:
            os.makedirs(d, exist_ok=True)

    def _check_bad_magic(self, err):
        if "bad magic" in (err or "").lower():
            self.log("\n>>> [CRITICAL ERROR]: 'Bad Magic' detected.")
            self.log(">>> You are using Watch Dogs 1 Gibbed tools on a Watch Dogs 2 file!")
            self.log(">>> Delete your current Gibbed tools and download the WD2-specific versions.\n")

    # ------------------------------------------------------------------
    # Deployed-state helpers
    # ------------------------------------------------------------------
    def is_deployed(self, game_data_dir):
        fat = os.path.join(game_data_dir, "patch3.fat")
        dat = os.path.join(game_data_dir, "patch3.dat")
        return os.path.exists(fat) or os.path.exists(dat)

    def restore_vanilla(self, game_data_dir):
        """Just deletes patch3 -- nothing to restore since nothing vanilla was ever overwritten."""
        removed = []
        errors = []
        for name in ("patch3.fat", "patch3.dat"):
            target = os.path.join(game_data_dir, name)
            if os.path.exists(target):
                try:
                    os.remove(target)
                    removed.append(name)
                except Exception as exc:
                    errors.append(f"{name}: {exc}")

        if errors:
            for e in errors:
                self.log(f"ERROR: Could not remove {e}")
            return False, f"Failed to remove: {', '.join(errors)}"

        if not removed:
            self.log("No patch3 files present. Game is already in a vanilla state.")
            return True, "Already vanilla -- nothing to remove."

        self.log(f"Removed {', '.join(removed)}. Game restored to vanilla state.")
        return True, f"Removed {len(removed)} file(s). Game is now vanilla."

    # ------------------------------------------------------------------
    # Vanilla reference data (read-only; used only to compute merges)
    # ------------------------------------------------------------------
    # read-only zone -- only patch3 ever gets written back to the game.
    # extraction is lazy, only happens once mods actually collide on a file.

    def _extracted_archives(self):
        found = []
        for archive in BASELINE_ARCHIVES:
            stem = os.path.splitext(archive)[0]
            target = os.path.join(self.base_dir, stem)
            if os.path.isdir(target) and any(os.scandir(target)):
                found.append(stem)
        return found

    def baseline_summary(self):
        """One-line view of merge readiness, rather than a per-archive to-do list."""
        stems = self._extracted_archives()
        files = 0
        for stem in stems:
            files += sum(len(f) for _, _, f in os.walk(os.path.join(self.base_dir, stem)))
        return {"archives": stems, "files": files, "ready": bool(stems)}

    def has_baseline(self):
        return bool(self._extracted_archives())

    def find_sample_fcb(self):
        """First .lib/.obj found, used as a converter test sample."""
        for root_dir in (self.base_dir, self.cache_dir):
            if not os.path.isdir(root_dir):
                continue
            for base, _, files in os.walk(root_dir):
                for f in files:
                    if os.path.splitext(f)[1].lower() in FCB_EXTENSIONS:
                        return os.path.join(base, f)
        return None

    def probe_converter(self):
        sample = self.find_sample_fcb()
        scratch = os.path.join(self.temp_dir, "probe")
        shutil.rmtree(scratch, ignore_errors=True)
        report = self.gibbed.probe_converter(sample or "", scratch)

        self.log("--- Converter diagnostic ---")
        self.log(f"  Executable present : {report['convert_exists']}")
        self.log(f"  projects/ folder   : {report['projects_folder']}"
                 f"{'' if report['projects_folder'] else '   <-- MISSING, likely the cause'}")
        self.log(f"  Sample file        : {os.path.basename(sample) if sample else '(none found)'}"
                 f"  ({report['original_bytes']:,} bytes)")
        if report["export_ok"]:
            self.log(f"  FCB -> XML         : OK via '{report['export_style']}' "
                     f"({report['xml_bytes']:,} bytes, {report['companion_files']} external file(s))")
        else:
            self.log("  FCB -> XML         : FAILED")
            for line in (report["export_error"] or "").splitlines():
                self.log(f"      {line}")
        if report["export_ok"]:
            if report["import_ok"]:
                self.log(f"  XML -> FCB         : OK via '{report['import_style']}' "
                         f"({report['roundtrip_bytes']:,} bytes vs {report['original_bytes']:,} original)")
                self.log("  RESULT: converter works -- merging is possible.")
            else:
                self.log("  XML -> FCB         : FAILED")
                for line in (report["import_error"] or "").splitlines():
                    self.log(f"      {line}")
                self.log("  RESULT: can read but not write. Merging impossible until fixed.")
        else:
            self.log("  RESULT: converter cannot read this game's files. Merging impossible.")
        self.log("--- end diagnostic ---")
        return report

    def roundtrip_ok(self, path):
        """
        checks whether gibbed can rebuild this file byte-identical. if not, any
        merge of it comes out corrupt -- recompile "succeeds" but the game just
        won't load it.

        usual culprit: two objects with the same name inside one library. the
        exporter writes the second as "Name (2).xml" and the importer then
        rejects it because the filename no longer matches the internal name.
        big overhaul mods hit this constantly.
        """
        try:
            key = (path, os.path.getsize(path), int(os.path.getmtime(path)))
        except OSError:
            return False
        if key in self._roundtrip_cache:
            return self._roundtrip_cache[key]

        scratch = os.path.join(self.temp_dir, "rtcheck")
        shutil.rmtree(scratch, ignore_errors=True)
        os.makedirs(scratch, exist_ok=True)
        xml_out = os.path.join(scratch, "chk_export.xml")
        fcb_out = os.path.join(scratch, "chk_rebuilt.fcb")

        ok = False
        try:
            good, _, _ = self.gibbed.fcb_to_xml(path, xml_out)
            if good:
                good, _, _ = self.gibbed.xml_to_fcb(xml_out, fcb_out)
                if good and os.path.exists(fcb_out):
                    if os.path.getsize(path) == os.path.getsize(fcb_out):
                        with open(path, "rb") as f1, open(fcb_out, "rb") as f2:
                            ok = f1.read() == f2.read()
        except Exception:
            ok = False
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

        self._roundtrip_cache[key] = ok
        return ok

    def probe_roundtrip(self, active_mods_ordered):
        """
        Export/import each contested file with zero merging involved, just to
        check the converter itself is lossless. Identical bytes -> any broken
        merge is our logic's fault and fixable. Different bytes -> that file
        can never be merged safely no matter how good the logic gets.
        """
        if not self.gibbed.can_convert():
            self.log("Converter unavailable.")
            return []

        file_matrix = {}
        for mod_name in active_mods_ordered:
            mod_path = os.path.join(self.cache_dir, mod_name)
            if not os.path.exists(mod_path):
                continue
            for root, _, files in os.walk(mod_path):
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, mod_path).replace("\\", "/")
                    if os.path.splitext(rel)[1].lower() in FCB_EXTENSIONS:
                        file_matrix.setdefault(rel, []).append((mod_name, full))

        contested = {r: e for r, e in file_matrix.items() if len(e) > 1}
        if not contested:
            self.log("No contested FCB files among the queued mods.")
            return []

        scratch = os.path.join(self.temp_dir, "roundtrip")
        shutil.rmtree(scratch, ignore_errors=True)
        os.makedirs(scratch, exist_ok=True)

        self.log("--- Round-trip test (export -> import, NO merging) ---")
        results = []
        for rel in sorted(contested):
            for mod_name, src in contested[rel]:
                xml_out = os.path.join(scratch, "rt_export.xml")
                fcb_out = os.path.join(scratch, "rt_rebuilt.fcb")
                for path in (xml_out, fcb_out,
                             self.gibbed.companion_dir(xml_out),
                             self.gibbed.companion_dir(fcb_out)):
                    if os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                    elif os.path.isfile(path):
                        os.remove(path)

                ok, _, err = self.gibbed.fcb_to_xml(src, xml_out)
                if not ok:
                    verdict, detail = "EXPORT FAILED", [l for l in (err or "").strip().splitlines()[:6]]
                else:
                    ok, _, err = self.gibbed.xml_to_fcb(xml_out, fcb_out)
                    if not ok:
                        verdict, detail = "IMPORT FAILED", [l for l in (err or "").strip().splitlines()[:6]]
                    else:
                        a = os.path.getsize(src)
                        b = os.path.getsize(fcb_out)
                        if a == b:
                            with open(src, "rb") as f1, open(fcb_out, "rb") as f2:
                                identical = f1.read() == f2.read()
                            verdict = "IDENTICAL" if identical else "SAME SIZE, BYTES DIFFER"
                        else:
                            verdict = f"SIZE DIFFERS ({a:,} -> {b:,})"
                        detail = []
                results.append((rel, mod_name, verdict))
                short = mod_name[:28]
                self.log(f"  {verdict:<26} {os.path.basename(rel):<34} {short}")
                for d in detail:
                    self.log(f"      {d}")

        bad = [r for r in results if r[2] != "IDENTICAL"]
        self.log("")
        if not bad:
            self.log("  All contested files round-trip perfectly. The converter is faithful,")
            self.log("  so any broken merged file is a merge-logic problem, not a tooling limit.")
        else:
            self.log(f"  {len(bad)} of {len(results)} did NOT round-trip cleanly. Those files")
            self.log("  cannot be safely merged by any logic -- force one mod to win them.")
        self.log("--- end round-trip test ---")
        return results

    def resolve_base_file(self, rel_path):
        """Vanilla version of rel_path, honouring archive precedence."""
        for archive in BASELINE_ARCHIVES:
            stem = os.path.splitext(archive)[0]
            candidate = os.path.join(self.base_dir, stem, rel_path.replace("/", os.sep))
            if os.path.isfile(candidate):
                return candidate
        return None

    def build_baseline(self, game_data_dir, needed_paths=None, force=False):
        """Unpacks vanilla archives for reference. With needed_paths given, stops
        as soon as everything's found -- most conflicts only cost one archive."""
        if not self.gibbed.is_ready():
            self.log("ERROR: Cannot read vanilla data -- Gibbed tools are missing.")
            return False, "Gibbed tools missing."

        if not os.path.isdir(game_data_dir):
            self.log(f"ERROR: data_win64 not found at '{game_data_dir}'.")
            return False, "Invalid game path."

        outstanding = set(needed_paths) if needed_paths else None
        if outstanding:
            outstanding = {p for p in outstanding if not self.resolve_base_file(p)}
            if not outstanding:
                return True, "Reference data already available."

        extracted_any = bool(self._extracted_archives())
        for archive in BASELINE_ARCHIVES:
            if outstanding is not None and not outstanding:
                break

            src = os.path.join(game_data_dir, archive)
            stem = os.path.splitext(archive)[0]
            target = os.path.join(self.base_dir, stem)

            if not os.path.exists(src):
                continue

            if os.path.isdir(target) and any(os.scandir(target)) and not force:
                if outstanding:
                    outstanding = {p for p in outstanding if not self.resolve_base_file(p)}
                extracted_any = True
                continue

            shutil.rmtree(target, ignore_errors=True)
            self.log(f"Reading vanilla reference from '{archive}' (read-only, may take a few minutes)...")
            success, _, err = self.gibbed.unpack_archive(src, target)
            if not success:
                self.log(f"ERROR: Could not read '{archive}': {err}")
                self._check_bad_magic(err)
                shutil.rmtree(target, ignore_errors=True)
                continue

            count = sum(len(f) for _, _, f in os.walk(target))
            if count == 0:
                shutil.rmtree(target, ignore_errors=True)
                continue

            self.log(f"  '{stem}' read ({count} files).")
            extracted_any = True

            if outstanding is not None:
                outstanding = {p for p in outstanding if not self.resolve_base_file(p)}
                if not outstanding:
                    self.log("  All required reference files found. Stopping early.")
                    break

        if not extracted_any:
            return False, "No vanilla archives could be read."
        if outstanding:
            self.log(f"WARN: {len(outstanding)} conflicting file(s) have no vanilla counterpart.")
        return True, "Vanilla reference data ready."

    def clean_temp(self):
        merging = os.path.join(self.temp_dir, "merging")
        shutil.rmtree(merging, ignore_errors=True)

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_extract_zip(zf, dest_dir):
        """
        zip-slip guard. extractall() will happily follow "../../../Windows/System32/evil.dll"
        or an absolute path right out of dest_dir -- mods come from random people
        on the internet, can't assume the archive plays nice.
        """
        dest_root = os.path.realpath(dest_dir)
        for member in zf.infolist():
            name = member.filename
            if name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
                raise ValueError(f"Refusing absolute path in archive: {name}")
            target = os.path.realpath(os.path.join(dest_root, name))
            if target != dest_root and not target.startswith(dest_root + os.sep):
                raise ValueError(f"Refusing path traversal in archive: {name}")
        zf.extractall(dest_dir)

    def extract_mod(self, archive_path, mod_name):
        """Returns True only when the mod actually landed in the cache."""
        dest = os.path.join(self.cache_dir, mod_name)
        raw_extract = os.path.join(self.temp_dir, f"raw_{mod_name}")
        corrected_root = None

        for d in [dest, raw_extract]:
            if os.path.exists(d):
                shutil.rmtree(d)
        os.makedirs(raw_extract, exist_ok=True)

        self.log(f"Extracting '{mod_name}'...")

        ext = os.path.splitext(archive_path)[1].lower()

        if ext == ".fat":
            self.log("Direct .fat file selected. Unpacking via Gibbed...")
            success, _, err = self.gibbed.unpack_archive(archive_path, raw_extract)
            if not success:
                self.log(f"ERROR: Failed to unpack .fat file: {err}")
                self._check_bad_magic(err)
                shutil.rmtree(raw_extract, ignore_errors=True)
                return False
        else:
            seven_zip_path = os.path.join(self.gibbed.tools_dir, "7za.exe")
            if os.path.exists(seven_zip_path):
                cmd = [seven_zip_path, "x", archive_path, f"-o{raw_extract}", "-y"]
                kwargs = {}
                if os.name == "nt":
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                try:
                    proc = subprocess.run(cmd, timeout=600, **kwargs)
                    if proc.returncode != 0:
                        self.log(f"ERROR: 7za failed to extract '{mod_name}' (code {proc.returncode}).")
                        shutil.rmtree(raw_extract, ignore_errors=True)
                        return False
                except subprocess.TimeoutExpired:
                    self.log(f"ERROR: 7za timed out extracting '{mod_name}'.")
                    shutil.rmtree(raw_extract, ignore_errors=True)
                    return False
            elif ext == ".zip":
                try:
                    with zipfile.ZipFile(archive_path, 'r') as zf:
                        self._safe_extract_zip(zf, raw_extract)
                except Exception as exc:
                    self.log(f"ERROR: Could not read zip '{mod_name}': {exc}")
                    shutil.rmtree(raw_extract, ignore_errors=True)
                    return False
            else:
                self.log(f"ERROR: Cannot extract {ext}. Please place '7za.exe' in your tools folder.")
                shutil.rmtree(raw_extract, ignore_errors=True)
                return False

        nested_fat = None
        for root, dirs, files in os.walk(raw_extract):
            for f in files:
                if f.endswith(".fat"):
                    nested_fat = os.path.join(root, f)
                    break
            if nested_fat:
                break

        if nested_fat:
            self.log("Detected a pre-packed .fat archive INSIDE the zip. Unpacking inner payload...")
            inner_extract = os.path.join(self.temp_dir, f"inner_unpacked_{mod_name}")
            success, _, err = self.gibbed.unpack_archive(nested_fat, inner_extract)
            if success:
                shutil.rmtree(raw_extract, ignore_errors=True)
                raw_extract = inner_extract
            else:
                self.log(f"ERROR: Failed to unpack inner .fat: {err}")
                self._check_bad_magic(err)
                shutil.rmtree(raw_extract, ignore_errors=True)
                shutil.rmtree(inner_extract, ignore_errors=True)
                return False

        valid_roots = {'generated', 'worlds', 'ui', 'sound', 'graphics', 'engine', 'actionmaps', 'scripts'}
        true_root = raw_extract
        found_engine_folder = False

        for root, dirs, files in os.walk(raw_extract):
            if any(d.lower() in valid_roots for d in dirs):
                true_root = root
                found_engine_folder = True
                break

            if any(f.endswith(('.fcb', '.lib', '.hkx', '.xbt', '.spk', '.lua', '.feu', '.xbg', '.xml')) for f in files):
                true_root = root
                break

        if not found_engine_folder:
            loose_configs = []
            for root, dirs, files in os.walk(true_root):
                for f in files:
                    if f.endswith(('.fcb', '.lib')):
                        loose_configs.append(os.path.join(root, f))

            if loose_configs:
                self.log("WARN: Poorly packed mod detected. Auto-correcting to 'generated/databases/generic/'.")
                corrected_root = os.path.join(self.temp_dir, f"corrected_{mod_name}")
                generic_path = os.path.join(corrected_root, "generated", "databases", "generic")
                os.makedirs(generic_path, exist_ok=True)

                for lf in loose_configs:
                    shutil.copy2(lf, os.path.join(generic_path, os.path.basename(lf)))

                true_root = corrected_root

        try:
            shutil.copytree(true_root, dest)
        except Exception as exc:
            self.log(f"ERROR: Could not cache '{mod_name}': {exc}")
            shutil.rmtree(raw_extract, ignore_errors=True)
            return False
        finally:
            shutil.rmtree(raw_extract, ignore_errors=True)
            if corrected_root and os.path.exists(corrected_root):
                shutil.rmtree(corrected_root, ignore_errors=True)

        if not os.path.isdir(dest):
            self.log(f"ERROR: '{mod_name}' produced no cached files.")
            return False

        self.log(f"Successfully processed and cached '{mod_name}'.")
        return True

    # ------------------------------------------------------------------
    # Mod content inspection (used by the UI to show what a mod actually
    # contains, and to detect overlaps between selected mods -- independent
    # of deploying)
    # ------------------------------------------------------------------
    def _build_file_matrix(self, mod_names, warn_missing=True):
        """rel_path -> [(mod_name, full_path), ...], in the given mod order."""
        matrix = {}
        for mod_name in mod_names:
            mod_path = os.path.join(self.cache_dir, mod_name)
            if not os.path.exists(mod_path):
                if warn_missing:
                    self.log(f"WARN: '{mod_name}' is queued but missing from the cache. Skipping.")
                continue
            for root, _, files in os.walk(mod_path):
                for f in files:
                    full_p = os.path.join(root, f)
                    rel_p = os.path.relpath(full_p, mod_path).replace("\\", "/")
                    matrix.setdefault(rel_p, []).append((mod_name, full_p))
        return matrix

    def list_mod_files(self, mod_name):
        """everything in a mod's cached folder."""
        mod_path = os.path.join(self.cache_dir, mod_name)
        out = []
        if not os.path.isdir(mod_path):
            return out
        for root, _, files in os.walk(mod_path):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, mod_path).replace("\\", "/")
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                out.append({"path": rel, "size": size})
        out.sort(key=lambda x: x["path"].lower())
        return out

    def compute_conflicts(self, mod_names):
        """Same overlap check deploy() uses internally, exposed standalone so
        the UI can show conflicts before you commit to actually deploying."""
        matrix = self._build_file_matrix(mod_names, warn_missing=False)
        conflicts = []
        for rel_path, entries in sorted(matrix.items()):
            if len(entries) < 2:
                continue
            ext = os.path.splitext(rel_path)[1].lower()
            conflicts.append({
                "path": rel_path,
                "mods": [m for m, _ in entries],
                "mergeable": ext in MERGEABLE_EXTENSIONS,
                "winner": entries[-1][0],
                "has_reference": self.resolve_base_file(rel_path) is not None,
            })
        return conflicts

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------
    def deploy(self, active_mods_ordered, game_data_dir, auto_reference=True,
               overrides=None, merging_enabled=True):
        self.log("--- Starting Atomic Deployment ---")

        if not self.gibbed.is_ready():
            status = self.gibbed.tools_status()
            missing = [v["name"] for k, v in status.items()
                       if isinstance(v, dict) and not v["ok"] and k in ("unpack", "pack")]
            self.log(f"ERROR: Required tools missing: {', '.join(missing)}. Aborting.")
            return False

        for d in [self.staging_dir, self.output_dir]:
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d, exist_ok=True)
        self.clean_temp()

        file_matrix = self._build_file_matrix(active_mods_ordered, warn_missing=True)

        if not file_matrix:
            self.log("ERROR: No files found across the queued mods. Nothing to deploy.")
            return False

        self.log(f"Found {len(file_matrix)} distinct files across {len(active_mods_ordered)} mods.")

        conflict_paths = [rel for rel, e in file_matrix.items()
                          if len(e) > 1 and os.path.splitext(rel)[1].lower() in MERGEABLE_EXTENSIONS]

        if conflict_paths and not merging_enabled:
            self.log(f"Merging is OFF -- {len(conflict_paths)} contested file(s) will be "
                     "resolved by rule or by load order, with no 3-way merging.")
        elif conflict_paths:
            unresolved = [p for p in conflict_paths if not self.resolve_base_file(p)]
            if unresolved:
                if auto_reference:
                    self.log(f"{len(conflict_paths)} file(s) edited by multiple mods. Reading the "
                             "vanilla versions so the edits can be merged instead of overwritten...")
                    self.build_baseline(game_data_dir, needed_paths=unresolved)
                else:
                    self.log("")
                    self.log(f">>> {len(conflict_paths)} file(s) are edited by more than one mod, but")
                    self.log(">>> vanilla reference data is unavailable, so their edits cannot be")
                    self.log(">>> merged -- the LAST mod in load order wins each file outright.")
                    self.log("")
        else:
            self.log("No overlapping files between mods -- no merging required.")

        for rel_path, entries in file_matrix.items():
            staging_out = os.path.join(self.staging_dir, rel_path)
            os.makedirs(os.path.dirname(staging_out), exist_ok=True)
            ext = os.path.splitext(rel_path)[1].lower()

            if len(entries) == 1:
                shutil.copy2(entries[0][1], staging_out)
                continue

            # per-file override always wins regardless of merge settings
            rule = (overrides or {}).get(rel_path)
            if rule and rule != "MERGE":
                match = next((e for e in entries if e[0] == rule), None)
                if match:
                    self.log(f"Rule: '{rel_path}' -> {rule} wins outright.")
                    shutil.copy2(match[1], staging_out)
                    continue
                self.log(f"WARN: Rule for '{rel_path}' names '{rule}', which isn't "
                         "among the queued mods. Falling back to default handling.")

            if ext not in MERGEABLE_EXTENSIONS or not merging_enabled:
                winner = entries[-1]
                label = "Asset conflict" if ext not in MERGEABLE_EXTENSIONS else "Merging off"
                self.log(f"{label} on '{rel_path}'. Winner: {winner[0]}")
                shutil.copy2(winner[1], staging_out)
                continue

            # gotta verify roundtrip first, merging a file the converter can't
            # rebuild faithfully just makes a corrupt archive
            unsafe = [n for n, p in entries
                      if ext in FCB_EXTENSIONS and not self.roundtrip_ok(p)]
            if unsafe:
                winner = entries[-1]
                self.log(f"NOT MERGEABLE: '{rel_path}' -- {', '.join(u[:28] for u in unsafe)} "
                         "cannot be rebuilt byte-for-byte by the converter "
                         "(usually duplicate object names inside the file).")
                self.log(f"      Merging it would silently corrupt the file, so "
                         f"'{winner[0][:28]}' wins it outright instead.")
                self.log("      Set a per-file rule in File Conflicts to choose a different mod.")
                shutil.copy2(winner[1], staging_out)
                continue

            self.log(f"Auto-merging '{rel_path}' across {len(entries)} mods...")
            try:
                self._merge_chain(rel_path, entries, staging_out, ext)
            except Exception as exc:
                # One bad file must not take the entire deployment down.
                self.log(f"ERROR: Merge of '{rel_path}' crashed: {exc}")
                self.log(f">>> Falling back to '{entries[-1][0]}' for this file.")
                try:
                    shutil.copy2(entries[-1][1], staging_out)
                except Exception as copy_exc:
                    self.log(f"ERROR: Could not even copy fallback: {copy_exc}")

        self.log("Compiling new patch archives to isolated workspace...")
        out_fat = os.path.join(self.output_dir, "patch3.fat")
        out_dat = os.path.join(self.output_dir, "patch3.dat")

        success, out, err = self.gibbed.pack_archive(self.staging_dir, out_fat)

        if not success or not os.path.exists(out_fat) or not os.path.exists(out_dat):
            self.log("ERROR: Packing failed! The original game files were NOT overwritten.")
            self.log(f"Gibbed Error Output: {err}")
            self._check_bad_magic(err)
            return False

        if os.path.getsize(out_fat) == 0 or os.path.getsize(out_dat) == 0:
            self.log("ERROR: Packed archives are empty! Aborting to protect game files.")
            return False

        self.log("Validation passed. Injecting modded patch into game directory...")
        try:
            shutil.copy2(out_fat, os.path.join(game_data_dir, "patch3.fat"))
            shutil.copy2(out_dat, os.path.join(game_data_dir, "patch3.dat"))
        except Exception as exc:
            self.log(f"ERROR: Could not write patch3 to game directory: {exc}")
            return False
        finally:
            self.clean_temp()

        self.log("Deployment complete! Game is ready to play.")
        return True

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Directory-aware merging
    # ------------------------------------------------------------------
    # library conversion produces a top-level XML plus a companion folder of
    # sub-object XMLs (often hundreds -- that's where most of the actual data
    # lives). merging only the top file would silently hand every sub-object
    # to one mod, so the companion trees get merged file-by-file too.

    @staticmethod
    def _xml_listing(directory):
        found = {}
        if directory and os.path.isdir(directory):
            for root, _, files in os.walk(directory):
                for name in files:
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, directory).replace(os.sep, "/")
                    found[rel] = full
        return found

    def _merge_companion_dirs(self, base_dir, a_dir, b_dir, out_dir,
                              priority_to_b=True, rel_path=""):
        """3-way merge of two companion trees. Returns (merged, added, removed)."""
        a_map = self._xml_listing(a_dir)
        b_map = self._xml_listing(b_dir)
        base_map = self._xml_listing(base_dir)

        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir, exist_ok=True)

        # empty companion folder = "nothing to say", not "deleted everything"
        deletions_meaningful = bool(a_map) and bool(b_map)

        # tally removals before applying any so we can bail if the count's nuts
        if deletions_meaningful and base_map:
            prospective = 0
            for rel in base_map:
                in_a, in_b = rel in a_map, rel in b_map
                if in_a and not in_b and priority_to_b:
                    prospective += 1
                elif in_b and not in_a and not priority_to_b:
                    prospective += 1
            if (len(base_map) >= DELETION_SAFETY_MIN_ENTRIES
                    and prospective > len(base_map) * DELETION_SAFETY_RATIO):
                deletions_meaningful = False
                pct = 100.0 * prospective / len(base_map)
                self.log(f"  >>> SAFETY: merging '{rel_path}' would delete {prospective} of "
                         f"{len(base_map)} vanilla sub-objects ({pct:.0f}%).")
                self.log("      That is too many to trust -- a mod shipping a partial file "
                         "looks the same as one deleting most of the database.")
                self.log("      Keeping all edits and additions, SKIPPING the removals. "
                         "Use a per-file rule in File Conflicts to force one mod instead.")

        merged = added = removed = 0
        for rel in sorted(set(a_map) | set(b_map)):
            a_path, b_path = a_map.get(rel), b_map.get(rel)
            base_path = base_map.get(rel)
            in_base = rel in base_map

            if in_base and deletions_meaningful:
                if a_path and not b_path and priority_to_b:
                    removed += 1
                    continue
                if b_path and not a_path and not priority_to_b:
                    removed += 1
                    continue

            dst = os.path.join(out_dir, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)

            if a_path and b_path:
                if rel.lower().endswith(".xml"):
                    try:
                        tree = self.merger.merge_two_mods(
                            base_path=base_path, mod_a_path=a_path,
                            mod_b_path=b_path, priority_to_b=priority_to_b,
                        )
                        tree.write(dst, encoding="utf-8", xml_declaration=True)
                        merged += 1
                        continue
                    except Exception:
                        pass  # unparseable -- fall through to a straight copy
                shutil.copy2(b_path if priority_to_b else a_path, dst)
                merged += 1
            else:
                shutil.copy2(a_path or b_path, dst)
                if not in_base:
                    added += 1

        # whatever the safety net spared needs to survive into the output too,
        # otherwise the XML and the folder drift apart again
        if not deletions_meaningful and base_map:
            for rel, src in base_map.items():
                dst = os.path.join(out_dir, rel.replace("/", os.sep))
                if not os.path.exists(dst) and rel not in a_map and rel not in b_map:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)

        return merged, added, removed

    @staticmethod
    def _xml_references(xml_path):
        """External sub-object files referenced by an XML (attributes or text)."""
        refs = set()
        try:
            root = ET.parse(xml_path).getroot()
        except Exception:
            return refs
        for el in root.iter():
            for value in el.attrib.values():
                if isinstance(value, str) and value.lower().endswith(".xml"):
                    refs.add(value.replace("\\", "/").strip())
            if el.text and el.text.strip().lower().endswith(".xml"):
                refs.add(el.text.strip().replace("\\", "/"))
        return refs

    def _repair_references(self, top_xml, out_dir, donors):
        """
        if the merge dropped a sub-object the XML still points to, gibbed's
        importer blows up with FileNotFoundException. walk the refs and pull
        anything missing back in from the donor trees.
        """
        restored = 0
        seen = set()
        queue = [top_xml]
        while queue:
            current = queue.pop()
            for ref in self._xml_references(current):
                if ref in seen:
                    continue
                seen.add(ref)
                target = os.path.join(out_dir, ref.replace("/", os.sep))
                if not os.path.isfile(target):
                    for donor in donors:
                        candidate = os.path.join(donor, ref.replace("/", os.sep)) if donor else None
                        if candidate and os.path.isfile(candidate):
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            shutil.copy2(candidate, target)
                            restored += 1
                            break
                if os.path.isfile(target):
                    queue.append(target)
        return restored

    def _merge_chain(self, rel_path, entries, final_staging_path, ext):
        temp_merge_dir = os.path.join(self.temp_dir, "merging")
        shutil.rmtree(temp_merge_dir, ignore_errors=True)
        os.makedirs(temp_merge_dir, exist_ok=True)

        # --- vanilla baseline -------------------------------------------
        base_source = self.resolve_base_file(rel_path)
        base_xml = None
        if base_source:
            if ext in FCB_EXTENSIONS:
                converted = os.path.join(temp_merge_dir, "base", "base.xml")
                success, _, err = self.gibbed.fcb_to_xml(base_source, converted)
                base_xml = converted if success and os.path.exists(converted) else None
                if base_xml is None:
                    self.log(f"WARN: Could not convert baseline for '{rel_path}': "
                             f"{(err or '').strip()}")
                    self.log("      Merge degrades to last-wins for this file.")
            else:
                base_xml = base_source
        else:
            self.log(f"WARN: No vanilla baseline for '{rel_path}'. Merge degrades to last-wins.")
        base_dir = self.gibbed.companion_dir(base_xml) if base_xml else None

        # --- convert every contributing mod ------------------------------
        converted = []
        for idx, (mod_name, mod_file_path) in enumerate(entries):
            mod_xml = os.path.join(temp_merge_dir, f"mod_{idx}", f"mod_{idx}.xml")
            if ext in FCB_EXTENSIONS:
                success, out, err = self.gibbed.fcb_to_xml(mod_file_path, mod_xml)
                if not success or not os.path.exists(mod_xml):
                    self.log(f"WARN: Failed to convert '{rel_path}' for mod '{mod_name}'.")
                    detail = (err or "").strip() or (out or "").strip() or "(no output from tool)"
                    self.log(f"Converter said: {detail}")
                    self.log(f">>> MERGE FAILED for '{rel_path}'. Falling back to "
                             f"'{entries[-1][0]}' winning outright, which DISCARDS the other "
                             "mod's version of this file. Set a rule in File Conflicts to choose.")
                    shutil.copy2(entries[-1][1], final_staging_path)
                    return
            else:
                mod_xml = mod_file_path
            converted.append((mod_name, mod_xml, self.gibbed.companion_dir(mod_xml)))

        # --- chain the merges, load order order, later mod wins ties ------
        cur_name, cur_xml, cur_dir = converted[0]
        totals = [0, 0, 0, 0]   # merged, added, removed, restored
        for idx in range(1, len(converted)):
            nxt_name, nxt_xml, nxt_dir = converted[idx]
            out_xml = os.path.join(temp_merge_dir, f"merged_{idx}", f"merged_{idx}.xml")
            os.makedirs(os.path.dirname(out_xml), exist_ok=True)

            merged_tree = self.merger.merge_two_mods(
                base_path=base_xml, mod_a_path=cur_xml,
                mod_b_path=nxt_xml, priority_to_b=True,
            )
            merged_tree.write(out_xml, encoding="utf-8", xml_declaration=True)

            out_dir = self.gibbed.companion_dir(out_xml)
            if os.path.isdir(cur_dir) or os.path.isdir(nxt_dir):
                m, a, r = self._merge_companion_dirs(base_dir, cur_dir, nxt_dir, out_dir,
                                                     True, rel_path)
                totals[0] += m
                totals[1] += a
                totals[2] += r
                # Keep the XML and its folder consistent before recompiling.
                restored = self._repair_references(out_xml, out_dir, [nxt_dir, cur_dir, base_dir])
                totals[3] += restored

            cur_name, cur_xml, cur_dir = nxt_name, out_xml, out_dir

        if any(totals):
            self.log(f"  sub-objects: {totals[0]} merged, {totals[1]} added, {totals[2]} removed")
            if totals[3]:
                self.log(f"  restored {totals[3]} sub-object(s) still referenced by the merged XML")
            base_count = len(self._xml_listing(base_dir)) if base_dir else 0
            if base_count and totals[2] > base_count * 0.5:
                self.log(f"  >>> WARNING: {totals[2]} of {base_count} vanilla sub-objects were "
                         "dropped. That is unusually high -- verify this file in game, or set "
                         "a per-file rule in File Conflicts if it misbehaves.")

        # --- recompile ----------------------------------------------------
        if ext in FCB_EXTENSIONS:
            success, out, err = self.gibbed.xml_to_fcb(cur_xml, final_staging_path)
            if not success or not os.path.exists(final_staging_path):
                self.log(f"WARN: Failed to recompile merged '{rel_path}'.")
                self.log(f"Converter said: {(err or '').strip() or (out or '').strip()}")
                self.log(f">>> Falling back to '{entries[-1][0]}' winning outright.")
                shutil.copy2(entries[-1][1], final_staging_path)
                return

            # recompile can "succeed" and still spit out garbage the engine can't
            # read, so shove it back through the exporter as a sanity check --
            # if the converter can't parse it, neither can the game.
            verify_dir = os.path.join(temp_merge_dir, "verify")
            shutil.rmtree(verify_dir, ignore_errors=True)
            os.makedirs(verify_dir, exist_ok=True)
            verify_xml = os.path.join(verify_dir, "verify_out.xml")
            ok, vout, verr = self.gibbed.fcb_to_xml(final_staging_path, verify_xml)
            if not ok:
                self.log(f">>> VALIDATION FAILED for merged '{rel_path}': the rebuilt file "
                         "cannot be read back by the converter.")
                detail = (verr or "").strip().splitlines()[:3]
                for line in detail:
                    self.log(f"      {line}")
                self.log(f">>> Discarding the merge; '{entries[-1][0]}' wins it outright.")
                shutil.copy2(entries[-1][1], final_staging_path)
                return

            original_sizes = [os.path.getsize(p) for _, p in entries if os.path.exists(p)]
            merged_size = os.path.getsize(final_staging_path)
            if original_sizes and merged_size < max(original_sizes) * 0.5:
                self.log(f">>> WARNING: merged '{rel_path}' is {merged_size:,} bytes vs "
                         f"{max(original_sizes):,} for the largest input. That is a big drop "
                         "-- verify this file in game.")
        else:
            shutil.copy2(cur_xml, final_staging_path)
