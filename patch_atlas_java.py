from pathlib import Path

import swebench.harness.dockerfiles.java as java


source = Path(java.__file__)
old = "https://dlcdn.apache.org/maven/mvnd/1.0.2/maven-mvnd-1.0.2-linux-amd64.zip"
new = "https://archive.apache.org/dist/maven/mvnd/1.0.2/maven-mvnd-1.0.2-linux-amd64.zip"
content = source.read_text()
if content.count(old) != 1:
    raise RuntimeError(f"Expected one mvnd URL in {source}")
source.write_text(content.replace(old, new))
