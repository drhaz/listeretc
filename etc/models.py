import os
import sys
import numbers
import warnings
from collections import OrderedDict

try:
    if sys.version_info >= (3, 9):
        # This exists in 3.8 but a different API
        import importlib.resources as pkg_resources
    else:
        raise ImportError
except ImportError:
    # Try backported to PY<37 `importlib_resources`.
    import importlib_resources as pkg_resources

import numpy as np
from astropy import units as u
from astropy.table import QTable
from astropy.utils.exceptions import AstropyUserWarning
from synphot import units, SourceSpectrum, SpectralElement, specio
from synphot.spectrum import BaseUnitlessSpectrum, Empirical1D

from .config import Conf

__all__ = ['Site', 'Telescope', 'Instrument']

class ETCError(Exception):
    pass

class Site:
    """Model for a site location and the atmosphere above it"""

    def __init__(self, name=None, altitude=None, latitude= None, longitude=None, **kwargs):
        self.name = name if name is not None else "Undefined"
        self.altitude = altitude * u.m if altitude is not None else altitude
        self.latitude = latitude
        self.longitude = longitude
        if 'transmission' in kwargs:
            modelclass = Empirical1D
            try:
                transmission = float(kwargs['transmission'])
                wavelengths = np.arange(300, 1501, 1) * u.nm
                throughput = len(wavelengths) * [transmission,]
                header = {}
            except ValueError:
                sky_file = os.path.expandvars(kwargs['transmission'])
                try:
                    header, wavelengths, throughput = specio.read_spec(sky_file, wave_col='lam', flux_col='trans', wave_unit=u.nm,flux_unit=u.dimensionless_unscaled)
                except KeyError:
                    # ESO-SM01 format; different column name for transmission and micron vs nm
                    header, wavelengths, throughput = specio.read_spec(sky_file, wave_col='lam', flux_col='flux', wave_unit=u.micron,flux_unit=u.dimensionless_unscaled)
            self.transmission = BaseUnitlessSpectrum(modelclass, points=wavelengths, lookup_table=throughput, keep_neg=False, meta={'header': header})

    def __mul__(self, other):
        if isinstance(other, Telescope):
            other = other.reflectivity
        if isinstance(other, Instrument):
            other = other.transmission
        newcls = self
        newcls.transmission = self.transmission.__mul__(other)
        return newcls

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        newcls = self
        newcls.transmission = self.transmission.__truediv__(other)
        return newcls

    def __repr__(self):
        return "[{}({})]".format(self.__class__.__name__, self.name)

    def __str__(self):
        return "{}: lon={}, lat={}, altitude={}".format(self.name, self.longitude, self.latitude, self.altitude)


class Telescope:
    def __init__(self, name=None, size=0, area=0, num_mirrors=2, **kwargs):
        self.name = name if name is not None else "Undefined"
        self.size = size * u.m
        self.area_unit = u.m * u.m
        self.area = area * self.area_unit
        self.num_mirrors = num_mirrors

        modelclass = Empirical1D
        reflectivity = kwargs.get('reflectivity',  0.91)    # Default value based on average of bare Al over 300-1200nm
        try:
            reflectivity = float(reflectivity)
            wavelengths = np.arange(300, 1501, 1) * u.nm
            refl = len(wavelengths) * [reflectivity,]
            header = {}
        except ValueError:
            file_path = os.path.expandvars(kwargs['reflectivity'])
            if not os.path.exists(file_path):
                file_path = pkg_resources.files('etc.data').joinpath(kwargs['reflectivity'])
            header, wavelengths, refl = specio.read_ascii_spec(file_path, wave_unit=u.nm, flux_unit='%')

        mirror_se = BaseUnitlessSpectrum(modelclass, points=wavelengths, lookup_table=refl, keep_neg=True, meta={'header': header})
        # Assume all mirrors are the same reflectivity and multiply together
        self.reflectivity = mirror_se
        for x in range(0, self.num_mirrors-1):
            self.reflectivity *= mirror_se

    def tpeak(self, wavelengths=None):
        """Calculate :ref:`peak bandpass throughput <synphot-formula-tpeak>`.

        Parameters
        ----------
        wavelengths : array-like, `~astropy.units.quantity.Quantity`, or `None`
            Wavelength values for sampling.
            If not a Quantity, assumed to be in Angstrom.
            If `None`, ``self.waveset`` is used.

        Returns
        -------
        tpeak : `~astropy.units.quantity.Quantity`
            Peak bandpass throughput.

        """
        x = self.reflectivity._validate_wavelengths(wavelengths)
        return self.reflectivity(x).max()

    def __mul__(self, other):
        newcls = self
        newcls.reflectivity = self.reflectivity.__mul__(other)
        return newcls

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        newcls = self
        newcls.reflectivity = self.reflectivity.__truediv__(other)
        return newcls

    def __repr__(self):
        return "[{}({})]".format(self.__class__.__name__, self.name)

    def __str__(self):
        return "{} (M1: {} diameter, {} area; {} mirrors)".format(self.name, self.size.to(u.m), self.area.to(self.area_unit), self.num_mirrors)


class Instrument:
    def __init__(self, name=None, inst_type="IMAGER", **kwargs):
        _ins_types = ["IMAGER", "SPECTROGRAPH"]
        self.name = name if name is not None else "Undefined"
        self.inst_type = inst_type.upper() if inst_type.upper() in _ins_types else "IMAGER"

        # Defaults assume a "standard" imager with a single lens, 2 AR coatings
        # on the front and back surfaces and no mirrors
        self.num_ar_coatings = kwargs.get('num_ar_coatings', 2)
        self.num_lenses = kwargs.get('num_inst_lenses', 1)
        self.num_mirrors = kwargs.get('num_inst_mirrors', 0)

        # Fused silica (for the prism) and fused quartz (for the CCD window)
        # turn out to have the same transmission...
        self.lens_trans = kwargs.get('inst_lens_trans', 0.9)

        self.mirror_refl = kwargs.get('inst_mirror_refl', 0.9925)
        # Transmission/Reflection values of optical elements coating
        self.ar_coating = kwargs.get('inst_ar_coating_refl', 0.99)

        transmission = self._compute_transmission()
        wavelengths = np.arange(300, 1501, 1) * u.nm
        trans = len(wavelengths) * [transmission,]
        header = {}
        self.transmission = SpectralElement(Empirical1D, points=wavelengths, lookup_table=trans, keep_neg=True, meta={'header': header})

        self.filterlist = kwargs.get('filterlist', [])
        self.filterset = OrderedDict()
        for filtername in self.filterlist:
            if filtername not in self.filterset:
                self.filterset[filtername] = self.set_bandpass_from_filter(filtername)

        ccd_qe = kwargs.get('ccd', 0.9)
        if not isinstance(ccd_qe, (u.Quantity, numbers.Number)):
            file_path = os.path.expandvars(ccd_qe)
            if not os.path.exists(file_path):
                file_path = pkg_resources.files('etc.data').joinpath(ccd_qe)
            header, wavelengths, throughput = specio.read_ascii_spec(file_path, wave_unit=u.nm, flux_unit=units.THROUGHPUT)
            if throughput.mean() > 1.0:
                throughput /= 100.0
                header['notes'] = 'Divided by 100.0 to convert from percentage'
            header['filename'] = ccd_qe
            self.ccd = BaseUnitlessSpectrum(Empirical1D, points=wavelengths, lookup_table=throughput, keep_neg=False, meta={'header': header})
        else:
            self.ccd = ccd_qe

    def _read_lco_filter_csv(self, csv_filter):
        """Reads filter transmission files in LCO Imaging Lab v1 format (CSV
        file with header and data)
        Returns an empty header dictionary and the wavelength and trensmission columns"""

        table = QTable.read(csv_filter, format='ascii.csv', header_start=0, data_start=64)
        table.rename_column('ILDIALCT', 'Wavelength')
        table['Wavelength'].unit = u.nm
        table.rename_column('ilab_v1', 'Trans_measured')
        table['Trans_measured'].unit = u.dimensionless_unscaled
        table.rename_column('FITS/CSV file dialect', 'Trans_filtered')

        return {}, table['Wavelength'], table['Trans_measured']


    def set_bandpass_from_filter(self, filtername):
        """Loads the specified <filtername> from the transmission profile file
        which is mapped via the etc.config.Conf() items.
        Returns a SpectralElement instance for the filter profile
        """

        if len(filtername) == 2 and filtername[1] == 'p':
            filtername = filtername[0]

        mapping = { 'u' : Conf.lco_u_file,
                    'g' : Conf.lco_g_file,
                    'r' : Conf.lco_r_file,
                    'i' : Conf.lco_i_file,
                    'z' : Conf.lco_zs_file,
                    'zs' : Conf.lco_zs_file,
                    'C2' : Conf.lco_c2_file,
                    'C3' : Conf.lco_c3_file,
                    'OH' : Conf.lco_oh_file,
                    'CN' : Conf.lco_cn_file,
                    'NH2': Conf.lco_nh2_file,
                    'CR' : Conf.lco_cr_file,
                    'U' : Conf.lco_U_file,
                    'B' : Conf.lco_B_file,
                    'V' : Conf.lco_V_file,
                    'R' : Conf.lco_R_file,
                    'I' : Conf.lco_I_file,
                    'WHT_U' : Conf.wht_U_file,
                    'WHT_B' : Conf.wht_B_file,
                    'WHT_V' : Conf.wht_V_file,
                    'WHT_R' : Conf.wht_R_file,
                    'WHT_I' : Conf.wht_I_file,
                  }
        filename = mapping.get(filtername, None)
        if filename is None:
            raise ETCError('Filter name {0} is invalid.'.format(filtername))
        if 'LCO_' in filename().upper() and '.csv' in filename().lower():
            file_path = pkg_resources.files('etc.data').joinpath(os.path.expandvars(filename()))
            print("Reading LCO iLab format")
            header, wavelengths, throughput  = self._read_lco_filter_csv(file_path)
        elif 'http://svo' in filename().lower():
            print("Reading from SVO filter service")
            header, wavelengths, throughput = specio.read_remote_spec(filename(), wave_unit=u.AA, flux_unit=units.THROUGHPUT)
        else:
            file_path = pkg_resources.files('etc.data').joinpath(os.path.expandvars(filename()))
            warnings.simplefilter('ignore', category = AstropyUserWarning)
            header, wavelengths, throughput = specio.read_ascii_spec(file_path, wave_unit=u.nm, flux_unit=units.THROUGHPUT)
        header['filename'] = filename
        header['descrip'] = filename.description
        meta = {'header': header, 'expr': filtername}

        return SpectralElement(Empirical1D, points=wavelengths, lookup_table=throughput, meta=meta)

    def throughput(self, filtername):
        """Returns the total throughput of optics+filter/grating+CCD"""

        if filtername not in self.filterlist or filtername not in self.filterset:
            raise ETCError('Filter name {0} is invalid.'.format(filtername))

        return self.filterset[filtername] * self.transmission * self.ccd

    def _compute_transmission(self):
        """This calculates the optical transmission of the instrument from lenses,
        mirrors and AR coatings. Assumes no/little wavelength dependence which
        is true for typical fused silica or quartz over most of optical/NIR regime
        see e.g. https://www.newport.com/n/optical-materials"""

        # Air-glass interfaces:
        throughput = self.ar_coating**self.num_ar_coatings
        # Transmissive optical elements
        throughput *= self.lens_trans**self.num_lenses
        # Reflective optical elements (Mirrors):
        throughput *= self.mirror_refl**self.num_mirrors

        return throughput

    def __repr__(self):
        return "[{}({})]".format(self.__class__.__name__, self.name)

