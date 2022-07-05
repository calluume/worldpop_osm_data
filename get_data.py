import requests, math, json

import geopandas as gpd
import matplotlib.pyplot as plt
import xml.etree.ElementTree as ET

from tqdm import tqdm
from time import sleep
from geopy.distance import geodesic
from os import remove, path, makedirs

def generate_geojson_grid(bounds, min_division=100, num_divisions=None, output_file='grid.geojson'):
    """
    Generates a grid with defined bounds in a GEOJSON format.
    :param bounds: Coordinates of 2 diagonally opposite corners of bounds
    :param min_division: Minimum size of a division in m, default of 100
    :param num_divisions: (2x2) array of number of x and y divisions, overrides min_division
    :param output_file: Name of final GEOJSON output file
    """

    if len(bounds) in [2, 4]:
        max_x, min_x = max([coor[0] for coor in bounds]), min([coor[0] for coor in bounds])
        max_y, min_y = max([coor[1] for coor in bounds]), min([coor[1] for coor in bounds])
    else:
        print('Invalid bounds')
        exit()

    height = geodesic([min_x, min_y], [min_x, max_y]).km * 1000
    width = geodesic([min_x, min_y], [max_x, min_y]).km * 1000

    divisions = []
    if num_divisions == None:
        for dimension, diff in zip([width, height], [max_x - min_x, max_y - min_y]):
            division_size = min_division
            num_divisions = dimension / division_size
            if not num_divisions.is_integer():
                division_size = min_division + (((num_divisions % 1) * min_division)/math.floor(num_divisions))
            divisions.append([math.floor(num_divisions), diff / math.floor(num_divisions), division_size])
    else:
        num_divisions.reverse()
        for dim_division, dimension, diff in zip(num_divisions, [width, height], [max_x - min_x, max_y - min_y]):
            divisions.append([dim_division, diff / dim_division, dimension / dim_division])

    geojson_grid = {"type":"FeatureCollection","features":[]}
    current_grid_coors = [min_x, min_y]
    for _ in range(divisions[0][0]):
        for _ in range(divisions[1][0]):
            curr_x, curr_y = current_grid_coors
            division_width, division_height = divisions[0][1], divisions[1][1]
            coors = [[curr_y, curr_x],
                     [curr_y, curr_x + division_width],
                     [curr_y + division_height, curr_x + division_width],
                     [curr_y + division_height, curr_x]]

            polygon = {"type":"Feature","properties":{},"geometry":{"type":"Polygon","coordinates": [coors]}}
            geojson_grid['features'].append(polygon)
            current_grid_coors[1] += division_height
        
        current_grid_coors[1] = min_y
        current_grid_coors[0] += division_width

    if not output_file.endswith('.geojson'): output_file += '.geojson'
    with open(output_file, 'w') as output:
        json.dump(geojson_grid, output, indent=4)

def visualise_gpd_data(geojson_file, data_key='population'):
    """
    Visualises property from GEOJSON data.
    :param geojson_file: GEOJSON file to visualise
    :param data_key: Data column to visualise
    """

    gpd_data = gpd.read_file(geojson_file)

    _, ax = plt.subplots(1, 1)
    gpd_data.plot(column=data_key, ax=ax, legend=True)
    plt.show(block=True)

def get_worldpop_data(geojson_file, year=2010, output_file=None, delete_original=True):
    """
    Fetches the WorldPop population data for each feature in a GEOJSON file.
    :param geojson_file: GEOJSON file to add data for
    :param year: Year for population statistics (2000-2020), default 2010
    :param output_file: Name of final GEOJSON output file with population data
    :param delete_original: Denotes whether to delete original geojson_file
    """
    
    with open(geojson_file, 'r') as f:
        geojson_data = json.load(f)

    print('Fetching population:\n  ↳ {0} regions\n'.format(len(geojson_data['features'])))

    pbar = tqdm(geojson_data['features'])
    for grid in pbar:
        str_feature = json.dumps(grid)
        str_feature = '{"type":"FeatureCollection","features":['+str_feature+']}'
        
        pbar.set_description("Sending initial request")
        pop_request_url = "https://api.worldpop.org/v1/services/stats?dataset=wpgppop&year={0}&geojson={1}&runasync=false".format(year, str_feature)
        request_response = requests.get(pop_request_url).json()
        status, is_error = request_response['status'], request_response['error']

        if 'data' in request_response.keys() and not is_error:
            grid_population = request_response['data']['total_population']

        elif status in ['created', 'started', 'finished'] and not is_error:
            pbar.set_description("Fetching data from task id")
            
            task_id = request_response['taskid']

            fetched, current_timeout = False, 10
            while not fetched:
                
                status_request_url = 'https://api.worldpop.org/v1/tasks/' + task_id
                status_response = requests.get(status_request_url).json()

                if 'data' in status_response.keys() and not status_response['error']:
                    fetched, grid_population = True, status_response['data']['total_population']

                elif status_response['status'] != 'finished' and not status_response['error']:
                    pbar.set_description("Unsuccessful, waiting {0}".format(current_timeout))
                    sleep(current_timeout)
                    current_timeout += 10
                
                elif status_response['error']:
                    print('\nError:\n'+status_response['error_message'])
                    exit()

        grid['properties']['population'] = grid_population
    
    if output_file == None: output_file = geojson_file[:-8] + '_pop.geojson'
    print("Fetched population data!\n  ↳ Saving to : '{0}'".format(output_file))
    with open(output_file, 'w') as output:
        json.dump(geojson_data, output, indent=4)

    if delete_original: remove(geojson_file)

def get_road_layout(bounds, output_file='roads.xml'):
    """
    Fetches OSM road layout within bounds and saves output as an xml file.
    :param bounds: Coordinates of 2 diagonally opposite corners of bounds
    :param output_file: Name of final XML output file with road layout
    """

    if len(bounds) in [2, 4]:
        max_x, min_x = max([coor[1] for coor in bounds]), min([coor[1] for coor in bounds])
        max_y, min_y = max([coor[0] for coor in bounds]), min([coor[0] for coor in bounds])

    overpass_query = "[out:xml];way({0},{1},{2},{3})['name']['highway']['area'!~'yes'];(._;>;);out;".format(min_y, min_x, max_y, max_x)
    
    overpass_url = "http://overpass-api.de/api/interpreter"
    response = requests.get(overpass_url, params={'data': overpass_query})
    with open(output_file, 'w') as output:
        output.write(response.text)
    
if __name__ == "__main__":

    subdirectory = 'results/exeter_data'
    if not path.exists(subdirectory): makedirs(subdirectory)
 
    grid_geojson_file = subdirectory+'/grid.geojson'
    pop_geojson_file = subdirectory+'/pop.geojson'
    roads_xml_file = subdirectory+'/roads.xml'

    grid_bounds = [[50.737069, -3.559872], [50.704257, -3.491951]]

    generate_geojson_grid(bounds=grid_bounds, min_division=200, output_file=grid_geojson_file)
    get_worldpop_data(grid_geojson_file, output_file=pop_geojson_file, delete_original=False)
    get_road_layout(grid_bounds, roads_xml_file)

    #visualise_gpd_data(pop_geojson_file)