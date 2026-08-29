# System Endpoints

## /update/release

This is a get endpoint that returns the latest release version of the application.

The structure is :
```json
{
  "backend": {
    "tag_name": "string",
    "html_url": "string",
    "created_at": "string",
    "body": "string",
    "current_version": "string",
    "env": "string",
    "cmnt": "string"
  },
  "frontend": {
    "tag_name": "string",
    "html_url": "string",
    "created_at": "string",
    "body": "string"
  }
}
```
As you can see, there is no cmnt, env, current_version in the frontend object. This is becuase the backend cant not get these values from the frontend. These values will be defined by the frontend.