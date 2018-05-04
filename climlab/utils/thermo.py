"""
A collection of function definitions to handle common
thermodynamic calculations for the atmosphere.
"""

from __future__ import division
import numpy as np
from climlab import constants as const


def potential_temperature(T,p):
    """Compute potential temperature for an air parcel.

    Input:  T is temperature in Kelvin
            p is pressure in mb or hPa
    Output: potential temperature in Kelvin.

    """
    theta = T*(const.ps/p)**const.kappa
    return theta

def theta(T,p):
    '''Convenience method, identical to thermo.potential_temperature(T,p).'''
    return potential_temperature(T,p)

def temperature_from_potential(theta,p):
    """Convert potential temperature to in-situ temperature.

    Input:  theta is potential temperature in Kelvin
            p is pressure in mb or hPa
    Output: absolute temperature in Kelvin.

    """
    T = theta/((const.ps/p)**const.kappa)
    return T

def T(theta,p):
    '''Convenience method, identical to thermo.temperature_from_potential(theta,p).'''
    return temperature_from_potential(theta,p)

def clausius_clapeyron(T):
    """Compute saturation vapor pressure as function of temperature T.

    Input: T is temperature in Kelvin
    Output: saturation vapor pressure in mb or hPa

    Formula from Rogers and Yau "A Short Course in Cloud Physics" (Pergammon Press), p. 16
    claimed to be accurate to within 0.1% between -30degC and 35 degC
    Based on the paper by Bolton (1980, Monthly Weather Review).

    """
    Tcel = T - const.tempCtoK
    es = 6.112 * np.exp(17.67*Tcel/(Tcel+243.5))
    return es

def qsat(T,p):
    """Compute saturation specific humidity as function of temperature and pressure.

    Input:  T is temperature in Kelvin
            p is pressure in hPa or mb
    Output: saturation specific humidity (dimensionless).

    """
    eps = const.Rd / const.Rv
    es = clausius_clapeyron(T)
    q = eps * es / (p - (1 - eps) * es )
    return q

def pseudoadiabat(T,p):
    """Compute the local slope of the pseudoadiabat at given temperature and pressure

    Inputs:   p is pressure in hPa or mb
              T is local temperature in Kelvin
    Output:   dT/dp, the rate of temperature change for pseudoadiabatic ascent
                in units of K / hPa

    The pseudoadiabat describes changes in temperature and pressure for an air
    parcel at saturation assuming instantaneous rain-out of the super-saturated water

    Formula consistent with eq. (2.33) from Raymond Pierrehumbert, "Principles of Planetary Climate"
    which nominally accounts for non-dilute effects, but computes the derivative
    dT/dpa, where pa is the partial pressure of the non-condensible gas.

    Integrating the result dT/dp treating p as total pressure effectively makes the dilute assumption.
    """
    esoverp = clausius_clapeyron(T) / p
    Tcel = T - const.tempCtoK
    L = (2.501 - 0.00237 * Tcel) * 1.E6   # Accurate form of latent heat of vaporization in J/kg
    ratio = L / T / const.Rv
    dTdp = (T / p * const.kappa * (1 + esoverp * ratio) /
        (1 + const.kappa * (const.cpv / const.Rv + (ratio-1) * ratio) * esoverp))
    return dTdp

def lifting_condensation_level(T, RH):
    '''Compute the Lifiting Condensation Level (LCL) for a given temperature and relative humidity

    Inputs:  T is temperature in Kelvin
            RH is relative humidity (dimensionless)

    Output: LCL in meters

    This is height (relative to parcel height) at which the parcel would become saturated during adiabatic ascent.

    Based on approximate formula from Bolton (1980 MWR) as given by Romps (2017 JAS)

    For an exact formula see Romps (2017 JAS), doi:10.1175/JAS-D-17-0102.1
    '''
    Tadj = T-55.  # in Kelvin
    return const.cp/const.g*(Tadj - (1/Tadj - np.log(RH)/2840.)**(-1))

def estimated_inversion_strength(T0,T700):
    '''Compute the Estimated Inversion Strength or EIS,
    following Wood and Bretherton (2006, J. Climate)

    Inputs: T0 is surface temp in Kelvin
           T700 is air temperature at 700 hPa in Kelvin

    Output: EIS in Kelvin

    EIS is a normalized measure of lower tropospheric stability acccounting for
    temperature-dependence of the moist adiabat.
    '''
    # Interpolate to 850 hPa
    T850 = (T0+T700)/2.;
    # Assume 80% relative humidity to compute LCL, appropriate for marine boundary layer
    LCL = lifting_condensation_level(T0, 0.8)
    # Lower Tropospheric Stability (theta700 - theta0)
    LTS = potential_temperature(T700, 700) - T0
    #  Gammam  = -dtheta/dz is the rate of potential temperature decrease along the moist adiabat
    #  in K / m
    Gammam = (const.g/const.cp*(1.0 - (1.0 + const.Lhvap*qsat(T850,850) /
              const.Rd / T850) / (1.0 + const.Lhvap**2 * qsat(T850,850)/
              const.cp/const.Rv/T850**2)))
    #  Assume exponential decrease of pressure with scale height given by surface temperature
    z700 = (const.Rd*T0/const.g)*np.log(1000./700.)
    return LTS - Gammam*(z700 - LCL)

def EIS(T0,T700):
    '''Convenience method, identical to thermo.estimated_inversion_strength(T0,T700)'''
    return estimated_inversion_strength(T0,T700)

def blackbody_emission(T):
    '''Blackbody radiation following the Stefan-Boltzmann law.'''
    return const.sigma * T**4

def Planck_frequency(nu, T):
    '''The Planck function B(nu,T):
    the flux density for blackbody radiation in frequency space
    nu is frequency in 1/s
    T is temperature in Kelvin

    Formula from Raymond Pierrehumbert, "Principles of Planetary Climate"

    '''
    h = const.hPlanck
    c = const.c_light
    k = const.kBoltzmann
    return 2*h*nu**3/c**2/(np.exp(h*nu/k/T)-1)

def Planck_wavenumber(n, T):
    '''The Planck function (flux density for blackbody radition)
    in wavenumber space
    n is wavenumber in 1/cm
    T is temperature in Kelvin

    Formula from Raymond Pierrehumbert, "Principles of Planetary Climate"

    '''
    c = const.c_light
    # convert to mks units
    n = n*100.
    return c * Planck_frequency(n*c, T)

def Planck_wavelength(l, T):
    '''The Planck function (flux density for blackbody radiation)
    in wavelength space
    l is wavelength in meters
    T is temperature in Kelvin

    Formula from Raymond Pierrehumbert, "Principles of Planetary Climate"

    '''
    h = const.hPlanck
    c = const.c_light
    k = const.kBoltzmann
    u = h*c/l/k/T
    return 2*k**5*T**5/h**4/c**3*u**5/(np.exp(u)-1)

def vmr_to_mmr(vmr, gas):
    '''
    Convert volume mixing ratio to mass mixing ratio for named gas.
    ( molecular weights are specific in climlab.utils.constants.py )
    '''
    return vmr * const.molecular_weight[gas] / const.molecular_weight['dry air']

def mmr_to_vmr(mmr, gas):
    '''
    Convert mass mixing ratio to volume mixing ratio for named gas.
    ( molecular weights are specific in climlab.utils.constants.py )
    '''
    return mmr * const.molecular_weight['dry air'] / const.molecular_weight[gas]
