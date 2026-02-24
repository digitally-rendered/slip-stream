# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability in slip-stream, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email **security@slip-stream.dev** (or open a private security advisory on GitHub) with:

1. A description of the vulnerability
2. Steps to reproduce
3. Potential impact
4. Suggested fix (if you have one)

You should receive an acknowledgment within 48 hours. We will work with you to understand the issue and coordinate a fix before any public disclosure.

## Scope

The following are in scope for security reports:

- **Injection vulnerabilities** in schema processing, query DSL, or endpoint generation
- **Authentication/authorization bypass** in the filter chain or policy engine
- **Data leakage** through the topology endpoint, schema vending API, or error responses
- **Denial of service** through malicious schemas, query payloads, or webhook configurations
- **Dependency vulnerabilities** in direct dependencies (fastapi, motor, pydantic, etc.)

## Security Considerations for Users

slip-stream generates API endpoints automatically from JSON schemas. As a framework author, you should be aware of:

- **Schema validation**: Always use `slip schema validate` before deploying schema changes
- **Filter chain**: Enable `AuthFilter` in production — endpoints are unauthenticated by default
- **Topology endpoint**: `/_topology` exposes app structure (schemas, filters, config). Consider restricting access in production
- **Query DSL**: The query DSL translates to MongoDB queries. While it sanitizes inputs, review complex filter expressions for your use case
- **MCP server**: The MCP write tools (create_schema, generate_sdk) modify the filesystem. Only run the MCP server in trusted environments
