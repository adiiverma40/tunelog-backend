# Changelog

## [unreleased]


### Changes:
- Added `.gitignore`

---

## 1.3.0
### New Features:
Added a function to fetch release details from github. Compare them and then show the user the latest release. So that user can update their app to the latest version.

### Docs 
- API docs boilerplate made.
- Added index page for API and backend 
- created a basic table for index
- Added basic doc for release fetch

### Added
- Added a `cache` for github release. Repeadely loading or switching page the dashboard, causes github network `Rate-limit`

### Changes:
- Added `fetch_release` function to `release_fetch.py`
- Added a caller for fetch_release in main function
- Updated docs for `fetch_release` 
- Added update endpoint for `fetch_release` at `/system/release`

### Bug fixes:
- Network error fixed.
- Key error fixed


> [!important]
> For devlopers only. I have switched from Default zed editor's python formatter to YAPF. The `.style.yapf` file is used to configure the formatter and included in the repository.

---

## 1.2.0

### New Feature:

This updates include a new configuration option for `listenbrainz playlist`. This option allows you to exclude songs with score less then 0 or timed out songs from the playlist.

The default value is `True`, which means that songs with score less then 0 or timed out songs will be excluded from the playlist.
The default value can be changed from the dashboard settings(work in progress).

If you want to change the default value and dashboard settings are not available then you can change it manually.

> Go to config folder, and edit the `Automation_config.json` file.
```json
"skip_song_if_less_score": true,
"skip_timeout_song": true
```

### Docs:
- Added `listenbrainz.md` to `Docs` directory for devlopers.
- Added docs for `Deep history and incremental sync`
- Added docs for `fetch_cf_batch` func.

### Resturcture:
- Changing the structure of the scrobble folder.
- Making a listenbrainz folder to put listenbrainz releated files.

### Changes:
- changed `fetch_cf_batch` to use user_id, before it was not using it.
- Added new config option for listenbrainz playlist.
- Added timeout support for Listenbrainz playlist.

### bugs:
- because i wrote `Feat: ` in commit message, the release didnt got triggered..... :(
---

## 1.1.3

### Bug:

The generateNotes job was running twice, once it fetches the changelog and then updated it and then pushed the changes back to the repository. After that it fetches back, because the clean up has already updated the `1.1.3` in changelog it returns and empty string, resulting in empty release note. **Fixed**

### Bug Fix:

- bad substitution: github action returned bad substitution error, fixed it.
- bad release note styling, fixed or tried to
- standard python print didnt gave the expected output. Trying sys.stdout.write instead.

### Changes:

- Added a python script to updated `1.1.0` to the exact version
- updated `publish-ghcr.yaml` to get release note from python script
- Deleted `test.py` it was no longer used
- Github publish and versioning
- child workflow permission error
- fetch-depth: 0 to avoid shallow clone
- switching to cycjimmy/semantic-release-action@v4
- token permision error during chekout
- node error, package.json
- Added APP_VERSION env var to Dockerfile

---

## 1.1.1

### Bug Fix:

- bad substitution: github action returned bad substitution error, fixed it.
- bad release note styling, fixed or tried to

### Changes:

- Added a python script to updated `1.1.0` to the exact version
- updated `publish-ghcr.yaml` to get release note from python script
- Deleted `test.py` it was no longer used
- Github publish and versioning
- child workflow permission error
- fetch-depth: 0 to avoid shallow clone
- switching to cycjimmy/semantic-release-action@v4
- token permision error during chekout
- node error, package.json
- Added APP_VERSION env var to Dockerfile

---

## 18th August 2026

### Changes:

- Support for manual installation

## 15th August 2026

**Happy independence day!**

### Bug:

- LB_worker uses issue, dafsfd

### Bug fix:

- Updating listenbrainz playlist to get listenbrainz cf with worker, using `.get()` for dict, instead of json way.

## 14th August 2026

### Bug:

- discovery playlist not taking user_id into account

### Fixed:

- discovery playlist taking user_id into account

### Changes:

- created new branch

## 13th August 2026

### Bug:

-

### Fixed:

-

### Changes:

- changed the response structure for auth routes to include current username

## 11th August 2026

### Bug:

- Ugghhh!! why is there always a bug.
- when fetching password from database, it returns user without password causing it to crash the decrypt function.

### Fixed:

- fixed by adding `where password is not null` in query

### Changes:

- Added gaurd in `decryptToken` to check is the token is none

## 10th August 2026

### Bug:

- **Issue #1**: When the user logs in for the 1st time it gets a ui error cause the backend does not have valid users in database

### Bug Fixed:

- **Issue fixed**: fixed the bug by fetching the users using the token of the user that is logging in, this code will only work. if there is no user in database, or the user logging in has its token expired, in that case, the token is refreshed for every user, making it work both ways.

### Changes

- Some cleanups

## 9th August 2026

### Bug Fixes

- I made it so the worker runs after the library sync happen to pass the none from table, but it made it so the sync library just updates the library with the new song id and hence the migration check passes

## 8th August 2026

### Changes

- Added example docker compose
