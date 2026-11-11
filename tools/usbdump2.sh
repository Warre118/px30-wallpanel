#!/bin/sh
# volledige GRF/PMUGRF/PMUCRU + USB-PHY-GRF (vergelijking Android <-> Debian)
R=${R:-./regdump}
$R ff140000 1000
$R ff010000 400
$R ff2bc000 100
$R ff2c0000 400
