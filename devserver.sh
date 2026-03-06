#!/bin/sh
source .venv/bin/activate
python -u -m flask --app main run --host=0.0.0.0 -p $PORT --debug