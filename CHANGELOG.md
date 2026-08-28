# CHANGELOG

<!-- version list -->

## v0.6.0 (2026-08-29)

### Bug Fixes

- Change authentication files location to user data dir and rename config.py to paths.py
  ([`d020e06`](https://github.com/snowflake36u/gdls/commit/d020e060ca4816a07d7fe73a293e3595bdd17dae))

- Ensure proper cleanup of logger handlers before adding new ones
  ([`074430d`](https://github.com/snowflake36u/gdls/commit/074430dcb14ec52cd0c47f82b2d4c5cf97dce1c4))

- Update import statements for progress-reporters to reflect new package name
  ([`2519977`](https://github.com/snowflake36u/gdls/commit/251997728580577c3fb73a4870d9a118e84cca5d))

### Documentation

- Update README to clarify features, usage, and authentication instructions
  ([`f708f3c`](https://github.com/snowflake36u/gdls/commit/f708f3c926a0341a473351544b0825b5aab62364))

### Features

- Expose gdls function in package and enhance argument validation
  ([`1773490`](https://github.com/snowflake36u/gdls/commit/1773490d01f0f8763091d8ed1816919ec2d84357))

- Introduce custom exceptions for better error handling and update validation logic
  ([`b28f123`](https://github.com/snowflake36u/gdls/commit/b28f123e64e3cc5537d3edc383bf457fcf7bad53))

### Refactoring

- Simplify user data directory resolution and update authentication path handling
  ([`ea7a023`](https://github.com/snowflake36u/gdls/commit/ea7a023ddda9a328f1e5bf76f18d15d97460512f))


## v0.5.0 (2026-08-25)

### Features

- Add relativeIdPath to item record fields
  ([`40c83ad`](https://github.com/snowflake36u/gdls/commit/40c83ad77deae3a90d6920d6faa8adc490353744))

### Testing

- Add simple unit tests for drive ID extraction and argument validation
  ([`dfc40b1`](https://github.com/snowflake36u/gdls/commit/dfc40b15f8489fbabd88dc2c6ac5f48b3b66227a))


## v0.4.1 (2026-08-25)

### Bug Fixes

- Enable progress bar updates for descendant count
  ([`f03c6a1`](https://github.com/snowflake36u/gdls/commit/f03c6a10ada1eeea9ab926ae0fc33b5b699e416b))


## v0.4.0 (2026-08-25)

### Bug Fixes

- Prevent progress bar updates when not initialized
  ([`a555c1b`](https://github.com/snowflake36u/gdls/commit/a555c1b3afc23a270673e730d5b0cf2d63bbd676))

### Chores

- **pyproject.toml**: Add configuration options for semantic_release to handle version below 1.0
  ([`8347037`](https://github.com/snowflake36u/gdls/commit/8347037b5d21d9649b756715b50a3872926ad20f))

### Features

- Add max depth argument for recursive item retrieval
  ([`a93e54d`](https://github.com/snowflake36u/gdls/commit/a93e54d580f2cd8259b240f17c44797c877d10b9))

- Normalize application name to lowercase
  ([`476d0a5`](https://github.com/snowflake36u/gdls/commit/476d0a54f400903319747af571397408966bc1da))


## v0.3.0 (2026-08-24)

- Initial Release
