"""Start scripts/run_local_openstreetmap.py with this package and its own libraries, and nothing from the shared Python's site-packages."""
import os
import runpy
import site
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, 'lib')
before = list(sys.path)
site.addsitedir(LIB)  # also reads the libraries' .pth files
sys.path[:] = [HERE, LIB] + [p for p in sys.path if p not in before and p != LIB] + before
sys.argv = [os.path.join(HERE, 'scripts/run_local_openstreetmap.py')] + sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
