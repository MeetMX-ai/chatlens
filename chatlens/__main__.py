import sys

if len(sys.argv) > 1 and sys.argv[1] not in (
    "--no-chatlog",
    "--no-decrypt",
    "--debug",
    "--host",
    "--port",
):
    from chatlens.plugins.cli.commands import main

    main()
else:
    from chatlens.main import main

    main()
