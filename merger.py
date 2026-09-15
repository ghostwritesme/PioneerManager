import os
import copy
import xml.etree.ElementTree as ET


class XMLTreeMerger:
    @staticmethod
    def load_root(path):
        if not path or not os.path.exists(path):
            return None
        try:
            return ET.parse(path).getroot()
        except Exception:
            return None

    # id attrs to try, in order. no id at all -> falls back to positional matching,
    # which basically breaks merging once two mods add/remove entries
    ID_ATTRIBUTES = ("Name", "name", "Hash", "hash", "Id", "id",
                     "file", "File", "path", "Path")

    # keep in sync with DELETION_SAFETY_RATIO in pipeline.py
    DELETION_SAFETY_RATIO = 0.25
    DELETION_SAFETY_MIN_ENTRIES = 50

    @classmethod
    def get_node_id(cls, node, index=0):
        for attr in cls.ID_ATTRIBUTES:
            value = node.attrib.get(attr)
            if value:
                return f"{node.tag}::{value}"
        return f"{node.tag}::{index}"

    def build_index(self, node, path=""):
        index = {}
        for idx, child in enumerate(node):
            node_id = self.get_node_id(child, idx)
            current_path = f"{path}/{node_id}"
            index[current_path] = child
            index.update(self.build_index(child, current_path))
        return index

    def merge_two_mods(self, base_path, mod_a_path, mod_b_path, priority_to_b=True,
                       allow_deletions=True):
        base_root = self.load_root(base_path)
        mod_a_root = self.load_root(mod_a_path)
        mod_b_root = self.load_root(mod_b_path)

        # no base = no way to know what a mod actually changed, so this just
        # falls back to last-mod-wins (not a real merge, see cache_base_game)
        if base_root is None:
            return ET.parse(mod_b_path if priority_to_b else mod_a_path)

        out_tree = ET.parse(base_path)
        out_root = out_tree.getroot()

        base_map = self.build_index(base_root)
        mod_a_map = self.build_index(mod_a_root) if mod_a_root is not None else {}
        mod_b_map = self.build_index(mod_b_root) if mod_b_root is not None else {}
        out_map = self.build_index(out_root)

        # gotta index the root too - companion-folder files are literally just
        # one object with no children, so skipping the root meant those never merged
        base_map[""] = base_root
        out_map[""] = out_root
        if mod_a_root is not None:
            mod_a_map[""] = mod_a_root
        if mod_b_root is not None:
            mod_b_map[""] = mod_b_root

        # shallow paths first so parents exist before we try to attach children to them
        all_paths = sorted(
            set(mod_a_map.keys()).union(set(mod_b_map.keys())),
            key=lambda p: p.count("/"),
        )

        for path in all_paths:
            base_node = base_map.get(path)
            node_a = mod_a_map.get(path)
            node_b = mod_b_map.get(path)
            target_node = out_map.get(path)

            if target_node is None:
                # new node, not an edit. used to just `continue` here and silently drop it
                source = node_b if (priority_to_b and node_b is not None) else node_a
                if source is None:
                    source = node_b
                if source is None:
                    continue

                parent_path = path.rsplit("/", 1)[0]
                parent = out_root if parent_path == "" else out_map.get(parent_path)
                if parent is None:
                    continue

                new_node = copy.deepcopy(source)
                parent.append(new_node)
                out_map[path] = new_node
                # reindex so nested additions under this new node still resolve
                for sub_path, sub_node in self.build_index(new_node).items():
                    out_map[f"{path}{sub_path}"] = sub_node
                continue

            diff_a = {k: v for k, v in node_a.attrib.items()
                      if base_node is None or base_node.attrib.get(k) != v} if node_a is not None else {}
            diff_b = {k: v for k, v in node_b.attrib.items()
                      if base_node is None or base_node.attrib.get(k) != v} if node_b is not None else {}

            for k, v in diff_a.items():
                target_node.attrib[k] = v

            for k, v in diff_b.items():
                if k in diff_a and not priority_to_b:
                    continue
                target_node.attrib[k] = v

            if node_a is not None and node_a.text and node_a.text.strip() != (
                    base_node.text.strip() if base_node is not None and base_node.text else ""):
                target_node.text = node_a.text
            if node_b is not None and node_b.text and node_b.text.strip() != (
                    base_node.text.strip() if base_node is not None and base_node.text else ""):
                if priority_to_b or node_a is None or not node_a.text:
                    target_node.text = node_b.text

        # ------------------------------------------------------------------
        # Deletion pass - vanilla node missing from a mod's file = mod deleted it.
        # without this, "remove content" mods merge to a no-op: additions/edits
        # apply but everything they tried to strip out silently comes back
        # ------------------------------------------------------------------
        # empty mod file is almost always a bad conversion, not "delete everything"
        degenerate = (mod_a_root is not None and len(mod_a_root) == 0) or \
                     (mod_b_root is not None and len(mod_b_root) == 0)
        if degenerate and allow_deletions and len(base_root) > 0:
            allow_deletions = False

        # if a mod dropped most of vanilla's entries it's probably a partial/differently
        # built file, not an intentional mass-delete - bail on deletions but keep edits/adds
        if allow_deletions and mod_a_root is not None and mod_b_root is not None:
            base_children = {self.get_node_id(c, i) for i, c in enumerate(base_root)}
            if base_children:
                a_children = {self.get_node_id(c, i) for i, c in enumerate(mod_a_root)}
                b_children = {self.get_node_id(c, i) for i, c in enumerate(mod_b_root)}
                dropped = base_children - (b_children if priority_to_b else a_children)
                if (len(base_children) >= self.DELETION_SAFETY_MIN_ENTRIES
                        and len(dropped) > len(base_children) * self.DELETION_SAFETY_RATIO):
                    allow_deletions = False

        if allow_deletions and mod_a_root is not None and mod_b_root is not None:
            parent_of = {}

            def index_parents(node, path=""):
                for idx, child in enumerate(node):
                    child_path = f"{path}/{self.get_node_id(child, idx)}"
                    parent_of[child_path] = node
                    index_parents(child, child_path)

            index_parents(out_root)

            to_delete = []
            for path in base_map:
                in_a = path in mod_a_map
                in_b = path in mod_b_map
                if in_a and in_b:
                    continue
                if not in_a and not in_b:
                    # both dropped it, easy case
                    to_delete.append(path)
                elif not in_a and in_b:
                    if not priority_to_b:
                        to_delete.append(path)
                elif in_a and not in_b:
                    # b kept it, a didn't - later mod (priority) wins either way
                    if priority_to_b:
                        to_delete.append(path)

            # deepest first or removing a parent breaks the child lookups below
            for path in sorted(to_delete, key=lambda p: p.count("/"), reverse=True):
                node = out_map.get(path)
                parent = parent_of.get(path)
                if node is not None and parent is not None:
                    try:
                        parent.remove(node)
                    except ValueError:
                        pass

        return out_tree
