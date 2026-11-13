#!/usr/bin/env bash

BUCKET=pinguinos-792440910424
aws s3 cp data/raw/penguins.csv s3://$BUCKET/data/raw/penguins.csv

aws s3api put-object --bucket $BUCKET --key reports/.keep