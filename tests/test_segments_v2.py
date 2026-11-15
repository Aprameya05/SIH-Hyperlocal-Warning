from satpy import Scene
import glob, numpy as np

files = glob.glob('HS_H09_20260725_1530_B13_FLDK_R20_S0*10.DAT.bz2')
scn = Scene(filenames=files, reader='ahi_hsd')
scn.load(['B13'])
bt = scn['B13'].values
lons, lats = scn['B13'].attrs['area'].get_lonlats()
valid = np.isfinite(bt) & np.isfinite(lons) & np.isfinite(lats)

VOBL_LAT, VOBL_LON = 13.1986, 77.7066
dist = np.where(valid, np.sqrt((lats-VOBL_LAT)**2+(lons-VOBL_LON)**2), 999)
idx = np.unravel_index(np.argmin(dist), dist.shape)
print(f'Nearest VOBL pixel: lat={lats[idx]:.3f} lon={lons[idx]:.3f} dist={dist[idx]*111:.1f}km BT={bt[idx]-273.15:.2f}C')

y0, x0 = int(idx[0]), int(idx[1])
r = 25
bt_box = bt[max(0,y0-r):y0+r, max(0,x0-r):x0+r]
bt_valid = bt_box[np.isfinite(bt_box)]
print(f'Valid pixels in 50km box: {len(bt_valid)}')
if len(bt_valid) > 0:
    min_bt_c = float(np.min(bt_valid)) - 273.15
    mean_bt_c = float(np.mean(bt_valid)) - 273.15
    cold_px = int(np.sum(bt_valid < 233.15))
    print(f'Min BT: {min_bt_c:.2f} C')
    print(f'Mean BT: {mean_bt_c:.2f} C')
    print(f'Cold pixels <-40C: {cold_px}')
    print(f'Storm detected: {min_bt_c < -40.0}')