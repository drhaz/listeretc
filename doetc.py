from etc.etc import ETC

import matplotlib.pyplot as plt
plt.ion()

LCO_Rho = ETC("etc/data/LCO_0m35_QHY600.toml")

filterlist = LCO_Rho.instrument.filterlist
LCO_Rho.plot(filterlist=filterlist[4:])

LCO_Rho.instrument.ccd_pixscale
LCO_Rho.instrument.ccd_fov()
LCO_Rho.instrument.ccd_fov(u.arcmin)

# compute snr in 4s on V=17.4
LCO_Rho.ccd_snr(4,17.4,filtername='LCO::V')