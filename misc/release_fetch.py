import os

import requests
from packaging.version import Version
from rich.console import Console

console = Console()
app_version = os.getenv("APP_VERSION", "unknown")

res_keys = ["tag_name", "html_url", "created_at", "body"]


def url(repo):
    return f"https://api.github.com/repos/{repo}/releases/latest"


def fetch_release_git():
    url_frontend = url("adiiverma40/tunelog-frontend")
    url_backend = url("adiiverma40/tunelog-backend")
    console.print("[blue]\\[Release]:: Fetching release info ::")
    backend_response = requests.get(url_backend)
    frontend_response = requests.get(url_frontend)

    return backend_response.json(), frontend_response.json()


def get_release_info(release):
    return {key: release.get(key) for key in res_keys}


def get_current_version(backend, f, cv):
    current_version = Version(cv if app_version == "unknown" else app_version)
    backend["current_version"] = (cv if app_version == "unknown" else app_version)
    release_version = Version(backend["tag_name"])
    console.print(f"[bold blue]\\[Release]:: Current Backend  version: [bold green]v{current_version}[/bold green] ::")
    console.print(f"[bold blue]\\[Release]:: Release Backend  version: [bold green]v{release_version}[/bold green] ::")
    console.print(f"[bold blue]\\[Release]:: Release Frontend version: [bold green]v{f['tag_name']}[/bold green] ::")

    if app_version == "unknown":
        # I wanted to log, switch to using docker, but it seems too annyoing to the windows user,
        # so I'm just printing a warning message instead.
        # # presonal bais: why are you using windows?
        console.print("[bold red]\\[release]:: you are not using docker, This may be incorrect!::")
        backend["env"] = "code"  # git clone
    else:
        backend["env"] = "docker"
    # print(backend)
    if current_version != release_version:
        if current_version < release_version:
            console.print(
                f"[bold blue]\\[Release]:: New version available: [bold green]v{release_version}[/bold green] ::")
            cv = current_version
            rv = release_version
            if cv.major < rv.major:
                console.print(
                    f"[bold blue]\\[Release]:: Major version update available: [bold green]v{rv}[/bold green] ::")
                backend["cmnt"] = "major"
            # No elif cause it will break if both major and minor are updated
            if cv.minor < rv.minor:
                console.print(
                    f"[bold blue]\\[Release]:: Minor version update available: [bold green]v{rv}[/bold green] ::")
                if backend["cmnt"]:
                    backend["cmnt"] += ", "
                backend["cmnt"] += "minor"
        else:
            console.print(
                f"[bold red]\\[Release]:: You have a newer version: [bold green]v{release_version}[/bold green]\nAre you a fellow contributor? I appriciate it. ::"
            )
    else:
        console.print("[bold green]\\[Release]:: You are using the latest version. ::")
        backend["cmt"] = "latest"

    return backend


def fetch_release(caller: str = "cli", current_version: str = "v0.0.0"):
    if caller == "api":
        console.quiet = True
    else:
        console.quiet = False
    backend, frontend = fetch_release_git()
    backend = get_release_info(backend)
    frontend = get_release_info(frontend)
    get_current_version(backend, frontend, current_version)
    # print(backend)
    return backend, frontend


fetch_release()
