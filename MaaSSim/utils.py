################################################################################
# Module: utils.py
# Reusable functions and methods used throughout the simulator
# Rafal Kucharski @ TU Delft
################################################################################

import pandas as pd
from dotmap import DotMap
import math
import random
import numpy as np
import os

from osmnx.distance import get_nearest_node
import osmnx as ox
import networkx as nx
import json
from matplotlib.collections import LineCollection

from .traveller import travellerEvent
from .driver import driverEvent


def dummy_False(*args, **kwargs):
    # dummy function to always return False,
    # used as default function inside of functionality
    # (if the behaviour is not modelled)
    return False


def dummy_True(*args, **kwargs):
    # dummy function to always return True
    return True


def rand_node(df):
    # returns a random node of a graph
    return df.loc[random.choice(df.index)].name


def generic_generator(generator, n):
    # to create multiple passengers/vehicles/etc
    return pd.concat([generator(i) for i in range(1, n + 1)], axis=1, keys=range(1, n + 1)).T


def empty_series(df, name=None):
    # returns empty Series from a given DataFrame, to be used for consistency of adding new rows to DataFrames
    if name is None:
        name = len(df.index) + 1
    return pd.Series(index=df.columns, name=name)


def initialize_df(df):
    # deletes rows in DataFrame and leaves the columns and index
    # returns empty DataFrame
    if type(df) == pd.core.frame.DataFrame:
        cols = df.columns
    else:
        cols = list(df.keys())
    df = pd.DataFrame(columns=cols)
    df.index.name = 'id'
    return df


def get_config(path):
    # reads a .json file with MaaSSim configuration
    # use as: params = get_config(config.json)
    with open(path) as json_file:
        data = json.load(json_file)
        return DotMap(data)


def save_config(_params, path=None):
    if path is None:
        path = os.path.join(_params.paths.params, _params.NAME + ".json")
    with open(path, "w") as write_file:
        json.dump(_params, write_file)


def set_t0(_params, now=True):
    if now:
        _params.t0 = pd.Timestamp.now().floor('1s')
    else:
        _params.t0 = pd.to_datetime(_params.t0)
    return _params


def networkstats(inData):
    """
    for a given network calculates it center of gravity (avg of node coordinates),
    gets nearest node and network radius (75th percentile of lengths from the center)
    returns a dictionary with center and radius
    """
    center_x = pd.DataFrame((inData.G.nodes(data='x')))[1].mean()
    center_y = pd.DataFrame((inData.G.nodes(data='y')))[1].mean()

    nearest = get_nearest_node(inData.G, (center_y, center_x))
    ret = DotMap({'center': nearest, 'radius': inData.skim[nearest].quantile(0.75)})
    return ret


def load_G(_inData, _params=None, stats=True, set_t=True):
    # loads graph and skim from a params paths
    if set_t:
        _params = set_t0(_params)
    _inData.G = ox.load_graphml(_params.paths.G)
    _inData.nodes = pd.DataFrame.from_dict(dict(_inData.G.nodes(data=True)), orient='index')
    skim = pd.read_csv(_params.paths.skim, index_col='Unnamed: 0')
    skim.columns = [int(c) for c in skim.columns]
    _inData.skim = skim
    if stats:
        _inData.stats = networkstats(_inData)  # calculate center of network, radius and central node
    return _inData


def generate_vehicles(_inData, nV):
    """
    generates single vehicle (database row with structure defined in DataStructures)
    index is consecutive number if dataframe
    position is random graph node
    status is IDLE
    """
    vehs = list()
    for i in range(nV + 1):
        vehs.append(empty_series(_inData.vehicles, name=i))

    vehs = pd.concat(vehs, axis=1, keys=range(1, nV + 1)).T
    vehs.event = driverEvent.STARTS_DAY
    vehs.platform = 0
    vehs.shift_start = 0
    vehs.shift_end = 60 * 60 * 24
    vehs.pos = vehs.pos.apply(lambda x: int(rand_node(_inData.nodes)))

    return vehs


def generate_demand(_inData, _params=None, avg_speed=False):
    # generates nP requests with a given temporal and spatial distribution of origins and destinations
    # returns _inData with dataframes requests and passengers populated.

    df = pd.DataFrame(index=np.arange(0, _params.nP), columns=_inData.passengers.columns)
    df.status = travellerEvent.STARTS_DAY
    df.pos = _inData.nodes.sample(_params.nP).index  # df.pos = df.apply(lambda x: rand_node(_inData.nodes), axis=1)
    _inData.passengers = df
    requests = pd.DataFrame(index=df.index, columns=_inData.requests.columns)
    distances = _inData.skim[_inData.stats['center']].to_frame().dropna()  # compute distances from center
    distances.columns = ['distance']
    distances = distances[distances['distance'] < _params.dist_threshold]
    # apply negative exponential distributions
    distances['p_origin'] = distances['distance'].apply(lambda x:
                                                        math.exp(
                                                            _params.demand_structure.origins_dispertion * x))

    distances['p_destination'] = distances['distance'].apply(
        lambda x: math.exp(_params.demand_structure.destinations_dispertion * x))
    if _params.demand_structure.temporal_distribution == 'uniform':
        treq = np.random.uniform(-_params.simTime * 60 * 60 / 2, _params.simTime * 60 * 60 / 2,
                                 _params.nP)  # apply uniform distribution on request times
    elif _params.demand_structure.temporal_distribution == 'normal':
        treq = np.random.normal(_params.simTime * 60 * 60 / 2,
                                _params.demand_structure.temporal_dispertion * _params.simTime * 60 * 60 / 2,
                                _params.nP)  # apply normal distribution on request times
    else:
        treq = None
    requests.treq = [_params.t0 + pd.Timedelta(int(_), 's') for _ in treq]
    requests.origin = list(
        distances.sample(_params.nP, weights='p_origin', replace=True).index)  # sample origin nodes from a distribution
    requests.destination = list(distances.sample(_params.nP, weights='p_destination',
                                                 replace=True).index)  # sample destination nodes from a distribution

    requests['dist'] = requests.apply(lambda request: _inData.skim.loc[request.origin, request.destination], axis=1)
    while len(requests[requests.dist >= _params.dist_threshold]) > 0:
        requests.origin = requests.apply(lambda request: (distances.sample(1, weights='p_origin').index[0]
                                                          if request.dist >= _params.dist_threshold else
                                                          request.origin),
                                         axis=1)
        requests.destination = requests.apply(lambda request: (distances.sample(1, weights='p_destination').index[0]
                                                               if request.dist >= _params.dist_threshold else
                                                               request.destination),
                                              axis=1)
        requests.dist = requests.apply(lambda request: _inData.skim.loc[request.origin, request.destination], axis=1)

    requests['ttrav'] = requests.apply(lambda request: pd.Timedelta(request.dist, 's').floor('s'), axis=1)
    # requests.ttrav = pd.to_timedelta(requests.ttrav)
    if avg_speed:
        requests.ttrav = (pd.to_timedelta(requests.ttrav) / _params.speeds.ride).dt.floor('1s')
    requests.tarr = [request.treq + request.ttrav for _, request in requests.iterrows()]
    requests = requests.sort_values('treq')
    requests.index = df.index
    requests.pax_id = df.index
    requests.shareable = False

    _inData.requests = requests
    _inData.passengers.pos = _inData.requests.origin

    _inData.passengers.platforms = _inData.passengers.platforms.apply(lambda x: [0])

    return _inData


def make_config_paths(params, main=None):
    # call it whenever you change a city name, or main path
    if main is None:
        main = os.path.join(os.getcwd(), "../..")
    params.paths.main = os.path.abspath(main)  # main repo folder
    params.paths.data = os.path.join(params.paths.main, 'data')  # data folder (not synced with repo)
    params.paths.params = os.path.join(params.paths.data, 'configs')
    params.paths.postcodes = os.path.join(params.paths.data, 'postcodes',
                                          "PC4_Nederland_2015.shp")  # PCA4 codes shapefile
    params.paths.albatross = os.path.join(params.paths.data, 'albatross')  # albatross data
    params.paths.sblt = os.path.join(params.paths.data, 'sblt')  # sblt results
    params.paths.G = os.path.join(params.paths.data, 'graphs',
                                  params.city.split(",")[0] + ".graphml")  # graphml of a current .city
    params.paths.skim = os.path.join(params.paths.main, 'data', 'graphs', params.city.split(",")[
        0] + ".csv")  # csv with a skim between the nodes of the .city
    params.paths.NYC = os.path.join(params.paths.main, 'data',
                                    'fhv_tripdata_2018-01.csv')  # csv with a skim between the nodes of the .city
    return params


def prep_supply_and_demand(_inData, params):
    _inData = generate_demand(_inData, params, avg_speed=True)
    _inData.vehicles = generate_vehicles(_inData, params.nV)
    _inData.vehicles.platform = _inData.vehicles.apply(lambda x: 0, axis=1)
    _inData.passengers.platforms = _inData.passengers.apply(lambda x: [0], axis=1)
    _inData.requests['platform'] = _inData.requests.apply(lambda row: _inData.passengers.loc[row.name].platforms[0],
                                                          axis=1)

    _inData.platforms = initialize_df(_inData.platforms)
    _inData.platforms.loc[0] = [1, 'Platform', 1]
    return _inData


#################
# PARALLEL RUNS #
#################


def test_space():
    # to see if code works
    full_space = DotMap()
    full_space.nP = [100, 200]  # number of requests per sim time
    return full_space


def slice_space(s, replications=1, _print=False):
    # util to feed the np.optimize.brute with a search space
    def sliceme(l):
        return slice(0, len(l), 1)

    ret = list()
    sizes = list()
    size = 1
    for key in s.keys():
        ret += [sliceme(s[key])]
        sizes += [len(s[key])]
        size *= sizes[-1]
    if replications > 1:
        sizes += [replications]
        size *= sizes[-1]
        ret += [slice(0, replications, 1)]
    print('Search space to explore of dimensions {} and total size of {}'.format(sizes, size)) if _print else None
    return tuple(ret)
