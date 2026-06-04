import re
import os

file_path = r"d:\AI_ML\virchow\web\src\components\icons\icons.tsx"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Remove all imports from @public
content = re.sub(r'import\s+\w+\s+from\s+["\']@public/[^"\']+["\'];\n', '', content)

# Change all `createLogoIcon(something...)` for the generic ones to `createIcon(FiFile)`
def replacer(match):
    name = match.group(1)
    return f"export const {name} = createIcon(FiFile);"

content = re.sub(r'export const (\w+Icon) = createLogoIcon[^;]+;', replacer, content)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Simplified icon exports to use FiFile generic icon instead of missing static images.")
