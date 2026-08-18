import fire
import sys 
changelog_path = "./changelog.md"


def note(v="0.0.0"):
    note = ""
    isNote = False
    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog = f.read()

        for line in changelog.split("\n"):
            if "[unreleased]" in line.lower():
                isNote = True
                line = line.replace("[unreleased]", f"{v}")
            if isNote:
                note += line + "\n"
            if "---" in line:
                break
    # print(note)
    sys.stdout.write(note.strip())


def postCleanup(v: str = "0.0.0"):
    changelog = ""
    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog = f.read()
        changelog = changelog.replace(
            "[unreleased]",
            f"{v}",
        )
    with open(changelog_path, "w", encoding="utf-8") as f:
        f.write(changelog)


# postCleanup()

if __name__ == "__main__":
    fire.Fire()
