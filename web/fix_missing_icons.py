import os
import re

src_dir = r"d:\AI_ML\virchow\web\src"
icons_file = r"d:\AI_ML\virchow\web\src\components\icons\icons.tsx"

with open(icons_file, "r", encoding="utf-8") as f:
    icons_content = f.read()

# Find all explicitly exported component names from icons.tsx
exported = set(re.findall(r'export\s+(?:const|function)\s+([A-Za-z0-9_]+)', icons_content))

# Collect all imports from "@/components/icons/icons"
imported = set()
for root, _, files in os.walk(src_dir):
    for filename in files:
        if filename.endswith(".ts") or filename.endswith(".tsx"):
            file_path = os.path.join(root, filename)
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                # Find import blocks from "@/components/icons/icons"
                matches = re.findall(r'import\s+\{([^}]+)\}\s+from\s+["\']@/components/icons/icons["\']', content)
                for block in matches:
                    parts = block.split(",")
                    for p in parts:
                        p = p.strip()
                        if p.startswith("type "):
                            p = p[5:].strip()
                        # handle 'as' aliases
                        if " as " in p:
                            p = p.split(" as ")[0].strip()
                        if p:
                            imported.add(p)

missing = imported - exported
print("Missing exports:", missing)

if missing:
    with open(icons_file, "a", encoding="utf-8") as f:
        f.write("\n// AUTO-ADDED MISSING EXPORTS\n")
        for m in sorted(list(missing)):
            # If it's a type or interface, we don't just mock it with a component
            if m.endswith("Props") or m == "VirchowIconType":
                continue
            f.write(f"export const {m} = createIcon(FiFile);\n")
    print("Added missing exports to icons.tsx")
