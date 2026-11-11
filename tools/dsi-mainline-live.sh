#!/bin/sh
# Bisect: DSI-host + PLL terug op mainline-waarden (240 Mbps), DPHY-lanetimings blijven Android.
W=/usr/local/sbin/regwrite
$W ff450004 0
$W ff2e000c 01
$W ff2e0010 14
$W ff450008 00000002
$W ff450018 00100004
$W ff450038 0000bf02
$W ff450048 00000013
$W ff45004c 0000001d
$W ff450050 000003ca
$W ff450068 010f7f00
$W ff450078 00000000
$W ff450098 00180030
$W ff45009c 11262710
$W ff450004 1
