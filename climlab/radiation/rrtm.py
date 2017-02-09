''' climlab wrapper for RRTMG_LW and RRTMG_SW radiation schemes

Here is a quick example of setting up a single-column Radiative-Convective model

```
import climlab
alb = 0.25
#  State variables (Air and surface temperature)
state = climlab.column_state(num_lev=30)
#  Parent model process
rcm = climlab.TimeDependentProcess(state=state)
#  Fixed relative humidity
h2o = climlab.radiation.ManabeWaterVapor(state=state)
#  Couple water vapor to radiation
rad = climlab.radiation.RRTMG(state=state, specific_humidity=h2o.q, albedo=alb)
#  Convective adjustment
conv = climlab.convection.ConvectiveAdjustment(state=state, adj_lapse_rate=6.5)
#  Couple everything together
rcm.add_subprocess('Radiation', rad)
rcm.add_subprocess('WaterVapor', h2o)
rcm.add_subprocess('Convection', conv)
#  Run the model
rcm.integrate_years(1)
#  Check for energy balance
print rcm.ASR - rcm.OLR
```

'''
from __future__ import division
import numpy as np
from climlab.process import TimeDependentProcess
from climlab.radiation.radiation import _Radiation_SW, _Radiation_LW
from climlab import constants as const
from climlab.utils.thermo import mmr_to_vmr
import _rrtmg_lw, _rrtmg_sw
from scipy.interpolate import interp1d


#  Get number of bands from fortran modules
nbndsw = int(_rrtmg_sw.parrrsw.nbndsw)
naerec = int(_rrtmg_sw.parrrsw.naerec)
nbndlw = int(_rrtmg_lw.parrrtm.nbndlw)


class RRTMG(_Radiation_SW, _Radiation_LW):
    '''Container to drive combined LW and SW radiation models.'''
    def __init__(self,
            # GENERAL, used in both SW and LW
            icld = 1,    # Cloud overlap method, 0: Clear only, 1: Random, 2,  Maximum/random] 3: Maximum
            irng = 1,  # more monte carlo stuff
            idrv = 0,  # whether to also calculate the derivative of flux with respect to surface temp
            permuteseed_sw =  150,  # used for monte carlo clouds; must differ from permuteseed_lw by number of subcolumns
            permuteseed_lw =  300,  # learn about these later...
            adjes = 1.,       # flux adjustment for earth/sun distance (if not dyofyr)
            dyofyr = 0,       # day of the year used to get Earth/Sun distance (if not adjes)
            # CLOUDS, SW see http://www.arm.gov/publications/proceedings/conf16/extended_abs/iacono_mj.pdf
            inflgsw  = 2, # Flag for cloud optical properties
                        # INFLAG = 0 direct specification of optical depths of clouds;
                        #            cloud fraction and cloud optical depth (gray) are
                        #            input for each cloudy layer
                        #        = 1 calculation of combined ice and liquid cloud optical depths (gray)
                        #            as in CCM2; cloud fraction and cloud water path are input for
                        #            each cloudy layer.
                        #        = 2 calculation of separate ice and liquid cloud optical depths, with
                        #            parameterizations determined by values of ICEFLAG and LIQFLAG.
                        #            Cloud fraction, cloud water path, cloud ice fraction, and
                        #            effective ice radius are input for each cloudy layer for all
                        #            parameterizations.  If LIQFLAG = 1, effective liquid droplet radius
                        #            is also needed.
            inflglw  = 2,
            iceflgsw = 1,  # Flag for ice particle specification
                        #             ICEFLAG = 0 the optical depths (gray) due to ice clouds are computed as in CCM3.
                        #                     = 1 the optical depths (non-gray) due to ice clouds are computed as closely as
                        #                         possible to the method in E.E. Ebert and J.A. Curry, JGR, 97, 3831-3836 (1992).
                        #                     = 2 the optical depths (non-gray) due to ice clouds are computed by a method
                        #                         based on the parameterization used in the radiative transfer model Streamer
                        #                         (reference,  J. Key, Streamer User's Guide, Technical Report 96-01] Boston
                        #                         University, 85 pp. (1996)), which is closely related to the parameterization
                        #                         of water clouds due to Hu and Stamnes (see below).
                        #             = 3 the optical depths (non-gray) due to ice clouds are computed by a method
                        # based on the parameterization given in Fu et al., J. Clim.,11,2223-2237 (1998).
                           # specific definition of reic depends on setting of iceflglw:
                           # iceflglw = 0,  ice effective radius, r_ec, (Ebert and Curry, 1992)]
                           #               r_ec must be >= 10.0 microns
                           # iceflglw = 1,  ice effective radius, r_ec, (Ebert and Curry, 1992)]
                           #               r_ec range is limited to 13.0 to 130.0 microns
                           # iceflglw = 2,  ice effective radius, r_k, (Key, Streamer Ref. Manual] 1996)
                           #               r_k range is limited to 5.0 to 131.0 microns
                           # iceflglw = 3,  generalized effective size, dge, (Fu, 1996)]
                           #               dge range is limited to 5.0 to 140.0 microns
                           #               [dge = 1.0315 * r_ec]

            iceflglw = 1,
            liqflgsw = 1,  # Flag for liquid droplet specification
                        # LIQFLAG = 0 the optical depths (gray) due to water clouds are computed as in CCM3.
                        #         = 1 the optical depths (non-gray) due to water clouds are computed by a method
                        #             based on the parameterization of water clouds due to Y.X. Hu and K. Stamnes,
                        #             J. Clim., 6, 728-742 (1993).
            liqflglw = 1,
            tauc_sw = 0.,  # In-cloud optical depth
            tauc_lw = 0.,  # in-cloud optical depth
            ssac_sw = 0.,  # In-cloud single scattering albedo
            asmc_sw = 0.,  # In-cloud asymmetry parameter
            fsfc_sw = 0.,  # In-cloud forward scattering fraction (delta function pointing forward "forward peaked scattering")
            # AEROSOLS
            tauaer_sw = 0.,   # Aerosol optical depth (iaer=10 only), Dimensions,  (ncol,nlay,nbndsw)] #  (non-delta scaled)
            ssaaer_sw = 0.,   # Aerosol single scattering albedo (iaer=10 only), Dimensions,  (ncol,nlay,nbndsw)] #  (non-delta scaled)
            asmaer_sw = 0.,   # Aerosol asymmetry parameter (iaer=10 only), Dimensions,  (ncol,nlay,nbndsw)] #  (non-delta scaled)
            ecaer_sw  = 0.,   # Aerosol optical depth at 0.55 micron (iaer=6 only), Dimensions,  (ncol,nlay,naerec)] #  (non-delta scaled)
            tauaer_lw = 0.,   # Aerosol optical depth at mid-point of LW spectral bands
            **kwargs):
        super(RRTMG, self).__init__(**kwargs)

        LW = RRTMG_LW(absorber_vmr = self.absorber_vmr,
                     cldfrac = self.cldfrac,
                     clwp = self.clwp,
                     ciwp = self.ciwp,
                     r_liq = self.r_liq,
                     r_ice = self.r_ice,
                     permuteseed = permuteseed_lw,
                     emissivity = self.emissivity,
                     inflglw = inflglw,
                     iceflglw = iceflglw,
                     liqflglw = liqflglw,
                     tauc = tauc_lw,
                     tauaer = tauaer_lw,
                     **kwargs)
        SW = RRTMG_SW(absorber_vmr = self.absorber_vmr,
                     cldfrac = self.cldfrac,
                     clwp = self.clwp,
                     ciwp = self.ciwp,
                     r_liq = self.r_liq,
                     r_ice = self.r_ice,
                     permute = permuteseed_sw,
                     aldif = self.aldif,
                     aldir = self.aldir,
                     asdif = self.asdif,
                     asdir = self.asdir,
                     cosZen = self.cosZen,
                     adjes = adjes,
                     dyofyr = dyofyr,
                     insolation = self.insolation,
                     inflgsw = inflgsw,
                     iceflgsw = iceflgsw,
                     tauc = tauc_sw,
                     ssac = ssac_sw,
                     asmc = asmc_sw,
                     fsfc = fsfc_sw,
                     tauaer = tauc_sw,
                     ssaaer = ssaaer_sw,
                     asmaer = asmaer_sw,
                     ecaer = ecaer_sw,
                     **kwargs)
        self.add_subprocess('SW', SW)
        self.add_subprocess('LW', LW )

        self.add_input('icld', icld)
        self.add_input('irng', irng)
        self.add_input('idrv', idrv)
        self.add_input('permuteseed_sw', permuteseed_sw)
        self.add_input('permuteseed_lw', permuteseed_lw)
        self.add_input('adjes', adjes)
        self.add_input('dyofyr', dyofyr)
        self.add_input('inflgsw', inflgsw)
        self.add_input('inflglw', inflglw)
        self.add_input('iceflgsw', iceflgsw)
        self.add_input('iceflglw', iceflglw)
        self.add_input('liqflgsw', liqflgsw)
        self.add_input('liqflglw', liqflglw)
        self.add_input('tauc_sw', tauc_sw)
        self.add_input('tauc_lw', tauc_lw)
        self.add_input('ssac_sw', ssac_sw)
        self.add_input('asmc_sw', asmc_sw)
        self.add_input('fsfc_sw', fsfc_sw)
        self.add_input('tauaer_sw', tauaer_sw)
        self.add_input('ssaaer_sw', ssaaer_sw)
        self.add_input('asmaer_sw', asmaer_sw)
        self.add_input('ecaer_sw', ecaer_sw)
        self.add_input('tauaer_lw', tauaer_lw)


    #  Some input values are defined as class properties so that
    #  user changes will be passed to LW and/or SW subprocesses
    #  NOT NEEDED ANYMORE BECAUSE OF HOW THE SUBPROCESSES ARE CREATED
    # @property
    # def permuteseed_sw(self):
    #     return self._permuteseed_sw
    # @permuteseed_sw.setter
    # def permuteseed_sw(self, value):
    #     self._permuteseed_sw = value
    #     self.subprocess['SW'].permuteseed = value
    # @property
    # def co2vmr(self):
    #     return self._co2vmr
    # @co2vmr.setter
    # def co2vmr(self, value):
    #     self._co2vmr = value
    #     self.subprocess['SW'].co2vmr = value
    #     self.subprocess['LW'].co2vmr = value
    #  Should add other gases and parameters here


class RRTMG_SW(_Radiation_SW):
    def __init__(self,
            # GENERAL, used in both SW and LW
            icld = 1,    # Cloud overlap method, 0: Clear only, 1: Random, 2,  Maximum/random] 3: Maximum
            irng = 1,  # more monte carlo stuff
            idrv = 0,  # whether to also calculate the derivative of flux with respect to surface temp
            permuteseed = 150,
            adjes = 1.,       # flux adjustment for earth/sun distance (if not dyofyr)
            dyofyr = 0,       # day of the year used to get Earth/Sun distance (if not adjes)
            #scon = const.S0/4,  # solar constant...  RRTMG_SW code has been modified to expect TOA insolation instead.
            inflgsw  = 2,
            iceflgsw = 1,
            liqflgsw = 1,
            tauc = 0.,
            ssac = 0.,  # In-cloud single scattering albedo
            asmc = 0.,  # In-cloud asymmetry parameter
            fsfc = 0.,  # In-cloud forward scattering fraction (delta function pointing forward "forward peaked scattering")
            # AEROSOLS
            tauaer = 0.,   # Aerosol optical depth (iaer=10 only), Dimensions,  (ncol,nlay,nbndsw)] #  (non-delta scaled)
            ssaaer = 0.,   # Aerosol single scattering albedo (iaer=10 only), Dimensions,  (ncol,nlay,nbndsw)] #  (non-delta scaled)
            asmaer = 0.,   # Aerosol asymmetry parameter (iaer=10 only), Dimensions,  (ncol,nlay,nbndsw)] #  (non-delta scaled)
            ecaer  = 0.,   # Aerosol optical depth at 0.55 micron (iaer=6 only), Dimensions,  (ncol,nlay,naerec)] #  (non-delta scaled)
            **kwargs):
        super(RRTMG_SW, self).__init__(**kwargs)
        #  define INPUTS
        self.add_input('icld', icld)
        self.add_input('irng', irng)
        self.add_input('idrv', idrv)
        self.add_input('permuteseed', permuteseed)
        self.add_input('adjes', adjes)
        self.add_input('dyofyr', dyofyr)
        self.add_input('inflgsw', inflgsw)
        self.add_input('iceflgsw', iceflgsw)
        self.add_input('liqflgsw', liqflgsw)
        self.add_input('tauc', tauc)
        self.add_input('ssac', ssac)
        self.add_input('asmc', asmc)
        self.add_input('fsfc', fsfc)
        self.add_input('tauaer', tauaer)
        self.add_input('ssaaer', ssaaer)
        self.add_input('asmaer', asmaer)
        self.add_input('ecaer', ecaer)
        #  define diagnostics
        self.add_diagnostic('ASR', 0.*self.Ts)
        self.add_diagnostic('ASRclr', 0.*self.Ts)
        self.add_diagnostic('TdotSW', 0.*self.Tatm)
        self.add_diagnostic('TdotSWclr', 0.*self.Tatm)

    def _prepare_sw_arguments(self):
        #  scalar integer arguments
        icld = self.icld
        irng = self.irng
        idrv = self.idrv
        permuteseed = self.permuteseed
        inflgsw = self.inflgsw
        iceflgsw = self.iceflgsw
        liqflgsw = self.liqflgsw
        dyofyr = self.dyofyr
        #  scalar real arguments
        adjes = self.adjes
        scon = self.insolation  # THIS IS A PROBLEM! RRTMG WANTS A SCALAR HERE, NOT AN ARRAY

        (ncol, nlay, play, plev, tlay, tlev, tsfc,
        h2ovmr, o3vmr, co2vmr, ch4vmr, n2ovmr, o2vmr, cfc11vmr,
        cfc12vmr, cfc12vmr, cfc22vmr, ccl4vmr,
        cldfrac, ciwp, clwp, relq, reic) = _prepare_general_arguments(self)

        aldif = _climlab_to_rrtm_sfc(self.aldif * np.ones_like(self.Ts))
        aldir = _climlab_to_rrtm_sfc(self.aldir * np.ones_like(self.Ts))
        asdif = _climlab_to_rrtm_sfc(self.asdif * np.ones_like(self.Ts))
        asdir = _climlab_to_rrtm_sfc(self.asdir * np.ones_like(self.Ts))
        coszen = _climlab_to_rrtm_sfc(self.cosZen * np.ones_like(self.Ts))
        #  THE REST OF THESE ARGUMENTS ARE STILL BEING HARD CODED.
        #   NEED TO FIX THIS UP...

        #  These arrays have an extra dimension for number of bands
        dim_sw1 = [nbndsw,ncol,nlay]     # [nbndsw,ncol,nlay]
        dim_sw2 = [ncol,nlay,nbndsw]  # [ncol,nlay,nbndsw]
        tauc = np.zeros(dim_sw1) # In-cloud optical depth
        ssac = np.zeros(dim_sw1) # In-cloud single scattering albedo
        asmc = np.zeros(dim_sw1) # In-cloud asymmetry parameter
        fsfc = np.zeros(dim_sw1) # In-cloud forward scattering fraction (delta function pointing forward "forward peaked scattering")

        # AEROSOLS
        tauaer = np.zeros(dim_sw2)   # Aerosol optical depth (iaer=10 only), Dimensions,  (ncol,nlay,nbndsw)] #  (non-delta scaled)
        ssaaer = np.zeros(dim_sw2)   # Aerosol single scattering albedo (iaer=10 only), Dimensions,  (ncol,nlay,nbndsw)] #  (non-delta scaled)
        asmaer = np.zeros(dim_sw2)   # Aerosol asymmetry parameter (iaer=10 only), Dimensions,  (ncol,nlay,nbndsw)] #  (non-delta scaled)
        ecaer  = np.zeros([ncol,nlay,naerec])   # Aerosol optical depth at 0.55 micron (iaer=6 only), Dimensions,  (ncol,nlay,naerec)] #  (non-delta scaled)

         #  new arguments for version 4.0
        isolvar = 0
        indsolvar = np.zeros(2)
        bndsolvar = np.zeros(nbndsw)
        solcycfrac = 0.

        args = [ncol, nlay, icld, permuteseed, irng, idrv, const.cp,
                play, plev, tlay, tlev, tsfc,
                h2ovmr, o3vmr, co2vmr, ch4vmr, n2ovmr, o2vmr,
                aldif, aldir, asdif, asdir, coszen, adjes, dyofyr, scon, isolvar,
                indsolvar, bndsolvar, solcycfrac,
                inflgsw, iceflgsw, liqflgsw,
                cldfrac, ciwp, clwp, reic, relq, tauc, ssac, asmc, fsfc,
                tauaer, ssaaer, asmaer, ecaer,]

        return args

    def _compute_heating_rates(self):
        '''Prepare arguments and call the RRTGM_SW driver to calculate
        radiative fluxes and heating rates'''
        args = self._prepare_sw_arguments()
        #  Call the RRTM code!
        swuflx, swdflx, swhr, swuflxc, swdflxc, swhrc = _rrtmg_sw.driver(*args)
        #  Output is all (ncol,nlay+1) or (ncol,nlay)
        self.flux_up = _rrtm_to_climlab(swuflx)
        self.flux_down = _rrtm_to_climlab(swdflx)
        self.flux_up_clr = _rrtm_to_climlab(swuflxc)
        self.flux_down_clr = _rrtm_to_climlab(swdflxc)
        #  hr is the heating rate in K/day from RRTMG_SW
        #  Need to set to W/m2
        Catm = self.Tatm.domain.heat_capacity
        self.heating_rate['Tatm'] = _rrtm_to_climlab(swhr) / const.seconds_per_day * Catm
        #  calculate slab ocean heating rate from flux divergence
        self.heating_rate['Ts'] = (self.flux_down[..., -1, np.newaxis] -
                                   self.flux_up[..., -1, np.newaxis])
        #  Set some diagnostics
        self.ASR = self.flux_down[..., 0, np.newaxis] - self.flux_up[..., 0, np.newaxis]
        self.ASRclr = self.flux_down_clr[..., 0, np.newaxis] - self.flux_up_clr[..., 0, np.newaxis]
        self.TdotSW = _rrtm_to_climlab(swhr)  # heating rate in K/day
        self.TdotSWclr = _rrtm_to_climlab(swhrc)


class RRTMG_LW(_Radiation_LW):
    def __init__(self,
            # GENERAL, used in both SW and LW
            icld = 1,    # Cloud overlap method, 0: Clear only, 1: Random, 2,  Maximum/random] 3: Maximum
            irng = 1,  # more monte carlo stuff
            idrv = 0,  # whether to also calculate the derivative of flux with respect to surface temp
            permuteseed =  300,
            inflglw  = 2,
            iceflglw = 1,
            liqflglw = 1,
            tauc = 0.,  # in-cloud optical depth
            tauaer = 0.,   # Aerosol optical depth at mid-point of LW spectral bands
            **kwargs):
        super(RRTMG_LW, self).__init__(**kwargs)
        #  define INPUTS
        self.add_input('icld', icld)
        self.add_input('irng', irng)
        self.add_input('idrv', idrv)
        self.add_input('permuteseed', permuteseed)
        self.add_input('inflglw', inflglw)
        self.add_input('iceflglw', iceflglw)
        self.add_input('liqflglw', liqflglw)
        self.add_input('tauc', tauc)
        self.add_input('tauaer', tauaer)
        #  define diagnostics
        self.add_diagnostic('OLR', 0.*self.Ts)
        self.add_diagnostic('OLRclr', 0.*self.Ts)
        self.add_diagnostic('TdotLW', 0.*self.Tatm)
        self.add_diagnostic('TdotLWclr', 0.*self.Tatm)

    def _prepare_lw_arguments(self):
        #  scalar integer arguments
        icld = self.icld
        irng = self.irng
        idrv = self.idrv
        permuteseed = self.permuteseed
        inflglw = self.inflglw
        iceflglw = self.iceflglw
        liqflglw = self.liqflglw

        (ncol, nlay, play, plev, tlay, tlev, tsfc,
        h2ovmr, o3vmr, co2vmr, ch4vmr, n2ovmr, o2vmr, cfc11vmr,
        cfc12vmr, cfc12vmr, cfc22vmr, ccl4vmr,
        cldfrac, ciwp, clwp, relq, reic) = _prepare_general_arguments(self)
        # surface emissivity
        emis = self.emissivity * np.ones((ncol,nbndlw))
        #  These arrays have an extra dimension for number of bands
        # in-cloud optical depth [nbndlw,ncol,nlay]
        tauc = _climlab_to_rrtm(self.tauc * np.ones_like(self.Tatm))
        #  broadcase to get [nbndlw,ncol,nlay]
        tauc = tauc * np.ones([nbndlw,ncol,nlay])
        # Aerosol optical depth at mid-point of LW spectral bands [ncol,nlay,nbndlw]
        tauaer = _climlab_to_rrtm(self.tauaer * np.ones_like(self.Tatm))
        #  broadcast and transpose to get [ncol,nlay,nbndlw]
        tauaer = np.transpose(tauaer * np.ones([nbndlw,ncol,nlay]), (1,2,0))
        args = [ncol, nlay, icld, permuteseed, irng, idrv, const.cp,
                play, plev, tlay, tlev, tsfc,
                h2ovmr, o3vmr, co2vmr, ch4vmr, n2ovmr, o2vmr,
                cfc11vmr, cfc12vmr, cfc22vmr, ccl4vmr, emis,
                inflglw, iceflglw, liqflglw,
                cldfrac, ciwp, clwp, reic, relq, tauc, tauaer,]
        return args

    def _compute_heating_rates(self):
        '''Prepare arguments and call the RRTGM_LW driver to calculate
        radiative fluxes and heating rates'''
        args = self._prepare_lw_arguments()
        #  Call the RRTM code!
        uflx, dflx, hr, uflxc, dflxc, hrc, duflx_dt, duflxc_dt = _rrtmg_lw.driver(*args)
        #  Output is all (ncol,nlay+1) or (ncol,nlay)
        self.flux_up = _rrtm_to_climlab(uflx)
        self.flux_down = _rrtm_to_climlab(dflx)
        self.flux_up_clr = _rrtm_to_climlab(uflxc)
        self.flux_down_clr = _rrtm_to_climlab(dflxc)
        #  hr is the heating rate in K/day from RRTMG_LW
        #  Need to set to W/m2
        Catm = self.Tatm.domain.heat_capacity
        self.heating_rate['Tatm'] = _rrtm_to_climlab(hr) / const.seconds_per_day * Catm
        #  calculate slab ocean heating rate from flux divergence
        self.heating_rate['Ts'] = (self.flux_down[..., -1, np.newaxis] -
                                   self.flux_up[..., -1, np.newaxis])
        #  Set some diagnostics
        self.OLR = self.flux_up[..., 0, np.newaxis]
        self.OLRclr = self.flux_up_clr[..., 0, np.newaxis]
        self.TdotLW = _rrtm_to_climlab(hr)  # heating rate in K/day
        self.TdotLWclr = _rrtm_to_climlab(hrc)  # heating rate in K/day


def _prepare_general_arguments(RRTMGobject):
    '''Prepare arguments needed for both RRTMG_SW and RRTMG_LW with correct dimensions.'''
    tlay = _climlab_to_rrtm(RRTMGobject.Tatm)
    tlev = _climlab_to_rrtm(interface_temperature(**RRTMGobject.state))
    play = _climlab_to_rrtm(RRTMGobject.lev * np.ones_like(tlay))
    plev = _climlab_to_rrtm(RRTMGobject.lev_bounds * np.ones_like(tlev))
    ncol, nlay = tlay.shape
    tsfc = _climlab_to_rrtm_sfc(RRTMGobject.Ts)
    # GASES -- put them in proper dimensions and units
    vapor_mixing_ratio = mmr_to_vmr(RRTMGobject.specific_humidity, gas='H2O')
    h2ovmr   = _climlab_to_rrtm(vapor_mixing_ratio * np.ones_like(RRTMGobject.Tatm))
    o3vmr    = _climlab_to_rrtm(RRTMGobject.absorber_vmr['O3'] * np.ones_like(RRTMGobject.Tatm))
    co2vmr   = _climlab_to_rrtm(RRTMGobject.absorber_vmr['CO2'] * np.ones_like(RRTMGobject.Tatm))
    ch4vmr   = _climlab_to_rrtm(RRTMGobject.absorber_vmr['CH4'] * np.ones_like(RRTMGobject.Tatm))
    n2ovmr   = _climlab_to_rrtm(RRTMGobject.absorber_vmr['N2O'] * np.ones_like(RRTMGobject.Tatm))
    o2vmr    = _climlab_to_rrtm(RRTMGobject.absorber_vmr['O2'] * np.ones_like(RRTMGobject.Tatm))
    cfc11vmr = _climlab_to_rrtm(RRTMGobject.absorber_vmr['CFC11'] * np.ones_like(RRTMGobject.Tatm))
    cfc12vmr = _climlab_to_rrtm(RRTMGobject.absorber_vmr['CFC12'] * np.ones_like(RRTMGobject.Tatm))
    cfc22vmr = _climlab_to_rrtm(RRTMGobject.absorber_vmr['CFC22'] * np.ones_like(RRTMGobject.Tatm))
    ccl4vmr  = _climlab_to_rrtm(RRTMGobject.absorber_vmr['CCL4'] * np.ones_like(RRTMGobject.Tatm))
    #  Cloud parameters
    cldfrac = _climlab_to_rrtm(RRTMGobject.cldfrac * np.ones_like(RRTMGobject.Tatm))
    ciwp = _climlab_to_rrtm(RRTMGobject.ciwp * np.ones_like(RRTMGobject.Tatm))
    clwp = _climlab_to_rrtm(RRTMGobject.clwp * np.ones_like(RRTMGobject.Tatm))
    relq = _climlab_to_rrtm(RRTMGobject.r_liq * np.ones_like(RRTMGobject.Tatm))
    reic = _climlab_to_rrtm(RRTMGobject.r_ice * np.ones_like(RRTMGobject.Tatm))

    return (ncol, nlay, play, plev, tlay, tlev, tsfc,
            h2ovmr, o3vmr, co2vmr, ch4vmr, n2ovmr, o2vmr, cfc11vmr,
            cfc12vmr, cfc12vmr, cfc22vmr, ccl4vmr,
            cldfrac, ciwp, clwp, relq, reic)



def interface_temperature(Ts, Tatm, **kwargs):
    '''Compute temperature at model layer interfaces.'''
    #  Actually it's not clear to me how the RRTM code uses these values
    lev = Tatm.domain.axes['lev'].points
    lev_bounds = Tatm.domain.axes['lev'].bounds
    #  Interpolate to layer interfaces
    f = interp1d(lev, Tatm, axis=-1)  # interpolation function
    Tinterp = f(lev_bounds[1:-1])
    #  add TOA value, Assume surface temperature at bottom boundary
    Ttoa = Tatm[...,0]
    Tinterp = np.concatenate((Ttoa[..., np.newaxis], Tinterp, Ts), axis=-1)
    return Tinterp

def _climlab_to_rrtm(field):
    '''Prepare field with proper dimension order.
    RRTM code expects arrays with (ncol, nlay)
    and with pressure decreasing from surface at element 0

    climlab grid dimensions are any of:
        - (num_lev,) --> (1, num_lev)
        - (num_lat, num_lev)  --> (num_lat, num_lev)
        - (num_lat, num_lon, num_lev)  -->  (num_lat*num_lon, num_lev)

        But lat-lon grids not yet supported here!

    Case single column
    '''
    # Make this work just with 1D (KM,) arrays
    #  (KM,)  -->  (1, nlay)
    try:
        #  Flip along the last axis to reverse the pressure order
        field = field[..., ::-1]
    except:
        if np.isscalar(field):
            return field
        else:
            raise ValueError('field must be array_like or scalar.')
    shape = field.shape
    if len(shape)==1:  #  (num_lev)
        #  Need to append an extra dimension for singleton horizontal ncol
        return field[np.newaxis, ...]
    elif len(shape)==2:  # (num_lat, num_lev)
        return field
    elif len(shape) > 2:
        raise ValueError('lat-lon grids not yet supported here.')
    #elif len(shape)==3:  # (num_lat, num_lon, num_lev)
        #  Need to reshape this array

def _rrtm_to_climlab(field):
    try:
        #  Flip along the last axis to reverse the pressure order
        field = field[..., ::-1]
    except:
        if np.isscalar(field):
            return field
        else:
            raise ValueError('field must be array_like or scalar.')
    return np.squeeze(field)

def _climlab_to_rrtm_sfc(field):
    if len(field.shape)==1:
        return field  #  single column
    elif len(field.shape)==2:
        return np.squeeze(field)  # should handle case with num_lat > 1
    else:
        raise ValueError('Mix up with dimensions of surface field')
