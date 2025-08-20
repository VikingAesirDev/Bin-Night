import os
import shutil

# Create directory structure
os.makedirs('static', exist_ok=True)

# Create a simple test HTML file
html_content = '''<!DOCTYPE html>
<html>
<head>
    <title>Bin Collection Test</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <h1>Testing Static Files</h1>
    <div id="test">If you can see this styled, static files work!</div>
    <script src="script.js"></script>
</body>
</html>'''

# Create a simple CSS file
css_content = '''body { font-family: Arial; background: #f0f0f0; }
h1 { color: #333; }
#test { color: green; font-weight: bold; }'''

# Create a simple JS file
js_content = '''console.log("JavaScript loaded successfully!");
document.addEventListener('DOMContentLoaded', function() {
    console.log("DOM loaded, app ready");
});'''

# Write files
with open('static/index.html', 'w') as f:
    f.write(html_content)
    
with open('static/styles.css', 'w') as f:
    f.write(css_content)
    
with open('static/script.js', 'w') as f:
    f.write(js_content)

print("Static files created successfully!")
print("File structure:")
for root, dirs, files in os.walk('.'):
    level = root.replace('.', '').count(os.sep)
    indent = ' ' * 2 * level
    print(f'{indent}{os.path.basename(root)}/')
    subindent = ' ' * 2 * (level + 1)
    for file in files:
        print(f'{subindent}{file}')
