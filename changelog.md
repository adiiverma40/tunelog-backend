# Changelog 

## [unreleased]

### Changes:
- Github publish and versioning 
- child workflow permission error \
- fetch-depth: 0 to avoid shallow clone
- switching to cycjimmy/semantic-release-action@v4\
- token permision error during chekout

## 18th August 2026

### Changes:
- Support for manual installation

## 15th August 2026
**Happy independence day!**

### Bug:
- LB_worker uses issue,   dafsfd

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

