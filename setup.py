from setuptools import setup, find_packages

# Read dependencies from requirements.txt
with open("requirements.txt") as f:
    requirements = f.read().splitlines()

setup(
    name="punchline_p2p",
    version="0.1",
    packages=find_packages(),
    install_requires=requirements,
    )
