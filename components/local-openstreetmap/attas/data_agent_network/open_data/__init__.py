"""Free public open-data APIs as Data Sources: USGS earthquakes, Our World in Data, Eurostat, WHO health data,
Taiwan's Central Weather Administration and Ministry of Environment, map tiles from NASA GIBS, EOX and OpenStreetMap,
OpenStreetMap vector tiles from VersaTiles, and map tiles read with each user's own key (Stadia Maps, MapTiler,
Thunderforest, Google Maps)."""
from .source import (EOXCloudlessDataSource, EurostatDataSource, GoogleMapDataSource, MapTilerDataSource, NASAGIBSDataSource,
                     OpenDataSource, OpenStreetMapDataSource, OurWorldInDataDataSource, SOURCES, StadiaMapsDataSource,
                     TaiwanCWADataSource, TaiwanMOENVDataSource, ThunderforestDataSource, USGSEarthquakesDataSource,
                     VersaTilesDataSource, WHOHealthDataSource, build_snapshot)

__all__ = ['EOXCloudlessDataSource', 'EurostatDataSource', 'GoogleMapDataSource', 'MapTilerDataSource', 'NASAGIBSDataSource',
           'OpenDataSource', 'OpenStreetMapDataSource', 'OurWorldInDataDataSource', 'SOURCES', 'StadiaMapsDataSource',
           'TaiwanCWADataSource', 'TaiwanMOENVDataSource', 'ThunderforestDataSource', 'USGSEarthquakesDataSource',
           'VersaTilesDataSource', 'WHOHealthDataSource', 'build_snapshot']
