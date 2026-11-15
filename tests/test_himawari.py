
from satpy import Scene
import glob

files = glob.glob('HS_H09_20260725_1530_B13_FLDK_R20_S0510.DAT.bz2')
scn = Scene(filenames=files, reader='ahi_hsd')
scn.load(['B13'])
print(scn['B13'])
print("Min BT:", float(scn['B13'].min()))
print("Max BT:", float(scn['B13'].max()))