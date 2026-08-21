# Misc code

This docs contains code for misc functions. functions that i was not able to decide where to place. Or it was not that complex to create an entire new module/folder.

## Release fetch

This function fetches the latest release from the GitHub API. After getting the release response. It gives the response data to the frontend. for displaying the latest release.

This has also a function to get the current docker version. Match it with the latest release version and compare either it is outdated or up to date.

```dockerfile
FROM python:3.14.3-alpine AS builder

RUN apk add --no-cache \
    build-base \
    libffi-dev

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt

FROM python:3.14.3-alpine

ARG APP_VERSION=unknown
ENV APP_VERSION=${APP_VERSION} # This is the version of the current docker image

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/deps

WORKDIR /app
COPY --from=builder /app/deps /app/deps
COPY . .

CMD ["python", "error.py"]
```
