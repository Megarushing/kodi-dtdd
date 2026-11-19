import os
import sys

# own package first: other add-ons also ship a 'resources.lib' package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dtdd_lib.service import run

run()
