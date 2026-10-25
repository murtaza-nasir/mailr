"""Optional command modules for the `mailr` command line.

Each module in this package that is present adds its own commands: it defines
`register(sub)`, which adds parsers to the top-level subparsers and returns
{command name: handler(args)}. A build without a module simply lacks its commands.
"""
