# Changelog 
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

