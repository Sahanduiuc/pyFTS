from pyFTS.common import fts, FuzzySet, FLR, Membership
from pyFTS.partitioners import Grid
from pyFTS.models.multivariate import FLR as MVFLR, common, flrg as mvflrg
from itertools import product

import numpy as np
import pandas as pd


def product_dict(**kwargs):
    '''
    Code by Seth Johnson
    :param kwargs:
    :return:
    '''
    keys = kwargs.keys()
    vals = kwargs.values()
    for instance in product(*vals):
        yield dict(zip(keys, instance))


class MVFTS(fts.FTS):
    """
    Multivariate extension of Chen's ConventionalFTS method
    """
    def __init__(self, **kwargs):
        super(MVFTS, self).__init__(**kwargs)
        self.explanatory_variables = kwargs.get('explanatory_variables',[])
        self.target_variable = kwargs.get('target_variable',None)
        self.flrgs = {}
        self.is_multivariate = True
        self.shortname = "MVFTS"
        self.name = "Multivariate FTS"

    def append_variable(self, var):
        """
        Append a new endogenous variable to the model

        :param var: variable object
        :return:
        """
        self.explanatory_variables.append(var)

    def format_data(self, data):
        ndata = {}
        for var in self.explanatory_variables:
            #ndata[var.name] = data[var.data_label]
            ndata[var.name] = var.partitioner.extractor(data[var.data_label])

        return ndata

    def apply_transformations(self, data, params=None, updateUoD=False, **kwargs):
        ndata = data.copy(deep=True)
        for var in self.explanatory_variables:
            if self.uod_clip and var.partitioner.type == 'common':
                ndata[var.data_label] = np.clip(ndata[var.data_label].values,
                                                var.partitioner.min, var.partitioner.max)

            ndata[var.data_label] = var.apply_transformations(ndata[var.data_label].values)

        return ndata

    def generate_lhs_flrs(self, data):
        flrs = []
        lags = {}
        for vc, var in enumerate(self.explanatory_variables):
            data_point = data[var.name]
            lags[var.name] = common.fuzzyfy_instance(data_point, var, tuples=False)

        for path in product_dict(**lags):
            flr = MVFLR.FLR()

            for var, fset in path.items():
                flr.set_lhs(var, fset)

            if len(flr.LHS.keys()) == len(self.explanatory_variables):
                flrs.append(flr)
            else:
                print(flr)

        return flrs

    def generate_flrs(self, data):
        flrs = []
        for ct in range(1, len(data.index)):
            ix = data.index[ct-1]
            data_point = self.format_data( data.loc[ix] )

            tmp_flrs = self.generate_lhs_flrs(data_point)

            target_ix = data.index[ct]
            target_point = data[self.target_variable.data_label][target_ix]
            target = common.fuzzyfy_instance(target_point, self.target_variable)

            for flr in tmp_flrs:
                for v, s in target:
                    flr.set_rhs(s)
                    flrs.append(flr)

        return flrs

    def generate_flrg(self, flrs):
        for flr in flrs:
            flrg = mvflrg.FLRG(lhs=flr.LHS)

            if flrg.get_key() not in self.flrgs:
                self.flrgs[flrg.get_key()] = flrg

            self.flrgs[flrg.get_key()].append_rhs(flr.RHS)


    def train(self, data, **kwargs):

        ndata = self.apply_transformations(data)

        flrs = self.generate_flrs(ndata)
        self.generate_flrg(flrs)

    def forecast(self, data, **kwargs):
        ret = []
        ndata = self.apply_transformations(data)
        c = 0
        for index, row in ndata.iterrows():
            data_point = self.format_data(row)
            flrs = self.generate_lhs_flrs(data_point)
            mvs = []
            mps = []
            for flr in flrs:
                flrg = mvflrg.FLRG(lhs=flr.LHS)
                if flrg.get_key() not in self.flrgs:
                    #Naïve approach is applied when no rules were found
                    if self.target_variable.name in flrg.LHS:
                        fs = flrg.LHS[self.target_variable.name]
                        fset = self.target_variable.partitioner.sets[fs]
                        mp = fset.centroid
                        mv = fset.membership(data_point[self.target_variable.name])
                        mvs.append(mv)
                        mps.append(mp)
                    else:
                        mvs.append(0.)
                        mps.append(0.)
                else:
                    mvs.append(self.flrgs[flrg.get_key()].get_membership(data_point, self.explanatory_variables))
                    mps.append(self.flrgs[flrg.get_key()].get_midpoint(self.target_variable.partitioner.sets))

            mv = np.array(mvs)
            mp = np.array(mps)

            ret.append(np.dot(mv,mp.T)/np.sum(mv))

        ret = self.target_variable.apply_inverse_transformations(ret,
                                                           params=data[self.target_variable.data_label].values)
        return ret

    def forecast_ahead(self, data, steps, **kwargs):
        generators = kwargs.get('generators',None)

        if generators is None:
            raise Exception('You must provide parameter \'generators\'! generators is a dict where the keys' +
                            ' are the variables names (except the target_variable) and the values are ' +
                            'lambda functions that accept one value (the actual value of the variable) '
                            ' and return the next value.')

        ndata = self.apply_transformations(data)

        ret = []
        for k in np.arange(0, steps):
            ix = ndata.index[-self.max_lag:]
            sample = ndata.loc[ix]
            tmp = self.forecast(sample, **kwargs)

            if isinstance(tmp, (list, np.ndarray)):
                tmp = tmp[-1]

            ret.append(tmp)

            last_data_point = sample.loc[sample.index[-1]]

            new_data_point = {}

            for var in self.explanatory_variables:
                if var.name != self.target_variable.name:
                    new_data_point[var.data_label] = generators[var.name](last_data_point[var.data_label])

            new_data_point[self.target_variable.data_label] = tmp

            ndata = ndata.append(new_data_point, ignore_index=True)

        return ret


    def clone_parameters(self, model):
        super(MVFTS, self).clone_parameters(model)

        self.explanatory_variables = model.explanatory_variables
        self.target_variable = model.target_variable

    def __str__(self):
        _str = self.name + ":\n"
        for k in self.flrgs.keys():
            _str += str(self.flrgs[k]) + "\n"

        return _str


