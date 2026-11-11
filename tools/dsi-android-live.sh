#!/bin/sh
# Live-test: DSI-host + DPHY-PLL exact op de Android-waarden (logs/dsi-android2.txt, dphy-android.txt).
W=/usr/local/sbin/regwrite
$W ff450004 0          # PWR_UP: host in reset
$W ff2e000c 04         # PLL prediv 4
$W ff2e0010 47         # PLL fbdiv 71 -> 12 MHz/4*71 = 213 MHz
$W ff450008 00000a02   # CLKMGR_CFG
$W ff450018 00040004   # DPI_LP_CMD_TIM
$W ff450038 00003f02   # VID_MODE_CFG (zonder LP_CMD_EN)
$W ff450048 00000011   # VID_HSA_TIME
$W ff45004c 00000019   # VID_HBP_TIME
$W ff450050 0000035c   # VID_HLINE_TIME
$W ff450068 000b0000   # CMD_MODE_CFG
$W ff450078 03e803e8   # TO_CNT_CFG
$W ff450098 00400040   # PHY_TMR_LPCLK_CFG
$W ff45009c 14102710   # PHY_TMR_CFG
$W ff450004 1          # PWR_UP
sleep 1
/usr/local/sbin/regdump ff4500b0 4
