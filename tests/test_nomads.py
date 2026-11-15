import requests
url = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?file=gfs.t00z.pgrb2.0p25.f012&lev_surface=on&lev_2_m_above_ground=on&lev_850_mb=on&lev_700_mb=on&lev_500_mb=on&lev_entire_atmosphere_%28considered_as_a_single_layer%29=on&var_TMP=on&var_RH=on&var_PRES=on&var_CAPE=on&var_CIN=on&var_PWAT=on&subregion=on&leftlon=77.08&rightlon=78.08&toplat=13.47&bottomlat=12.47&dir=%2Fgfs.20260726%2F00%2Fatmos"
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
r = requests.get(url, headers=headers, timeout=120)
print(f"Status: {r.status_code}")
print(f"Size: {len(r.content)} bytes")
print(f"First 500 chars: {r.text[:500]}")