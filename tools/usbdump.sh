#!/bin/sh
# dump USB-relevante registers (PHY-GRF, dwc2, CRU, GRF)
R=${R:-./regdump}
$R ff2c0000 400
$R ff2c8000 40
$R ff300000 140
$R ff300800 40
$R ff300900 20
$R ff300b00 20
$R ff300e00 4
$R ff2b0000 300
$R ff140000 600
