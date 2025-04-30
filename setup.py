from setuptools import setup, find_packages

# Read dependencies from requirements.txt
with open('requirements.txt') as f:
    requirements = f.read().splitlines()

with open('README.md') as f:
    readme = f.read()

setup(
    name='punchline_p2p',
    version='0.0.1',
    description='A peer-to-peer communication library',
    long_description=readme,
    long_description_content_type='text/markdown',
    author='Simon R.',
    author_email='',
    url='https://github.com/s-aluma-r/punchline-p2p',
    license='MIT',
    packages=find_packages(),
    install_requires=requirements,
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.6'  # needed?
    )
