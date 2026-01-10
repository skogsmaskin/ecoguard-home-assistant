# Copilot Instructions

This directory contains instructions for GitHub Copilot to help it better understand and work with this repository.

## Available Instructions

### 1. general.instructions.md
General project overview, code style, conventions, and best practices for the EcoGuard Home Assistant integration.

**Key Topics:**
- Project structure and organization
- Code style and naming conventions
- Commit message guidelines (Conventional Commits)
- Dependencies and common patterns
- Testing and documentation standards

### 2. home-assistant.instructions.md
Home Assistant-specific patterns and integration guidelines.

**Key Topics:**
- Integration type and configuration
- Config entry setup with runtime_data
- Sensor architecture and types
- Entity naming conventions
- Data coordinators and update intervals
- Translation and localization
- Nord Pool integration
- Home Assistant Quality Scale compliance

### 3. python.instructions.md
Python development guidelines and coding standards.

**Key Topics:**
- Type hints and async/await patterns
- Error handling and custom exceptions
- Logging best practices
- Data classes and context managers
- String formatting and None checks
- Code documentation
- Testing considerations

### 4. testing.instructions.md
Testing guidelines and best practices using pytest.

**Key Topics:**
- Test framework setup (pytest, pytest-homeassistant-custom-component)
- Running tests and coverage reporting
- Test fixtures and mocking patterns
- Test categories (unit, integration)
- Test assertions and verification
- AAA pattern and parametrized tests
- Async testing
- CI/CD considerations

### 5. api-architecture.instructions.md
EcoGuard API integration and architecture patterns.

**Key Topics:**
- API endpoints and authentication
- Data models and structures
- Coordinator architecture
- Caching strategy and request deduplication
- Sensor factory pattern
- Error handling strategy
- Data processing (aggregation, accumulation)
- Price estimation algorithms
- Translation and localization
- Performance optimization

## Usage

GitHub Copilot automatically reads these instruction files when working in this repository. They help Copilot:
- Understand project conventions and patterns
- Generate code that follows the project's style
- Suggest appropriate solutions based on the architecture
- Provide context-aware completions

## Contributing

When adding new patterns or conventions to the project, consider updating these instructions to help future contributors and improve Copilot's assistance.
