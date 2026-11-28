# Explicit build setup only. Runtime evaluation never pulls or installs anything.
FROM node@sha256:be979e5523c76fa4d5ca4ebd3099078f220aa47ec39c48ad5b5df9314debd6eb AS node
FROM python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
COPY --from=node /usr/local/bin/node /usr/local/bin/node
