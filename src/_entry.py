"""PyInstaller entry point for the magenda binary."""
import sys

from magenda.server import main

if __name__ == "__main__":
    sys.exit(main())
