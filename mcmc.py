import numpy as np
from scipy.optimize import curve_fit
from itertools import combinations
from scipy.misc import comb
import matplotlib.pyplot as plt
plt.ion()
import pickle
import glob
import msgpack
import msgpack_numpy as m
m.patch()

class MCMC:
    def __init__(self, x, y, model, savepath=None, cost=None, pnames=None):
        '''
        x, y      - Data to be fit.
                    type(x) and type(x) should be an array, list, or other array-like object.
        
        model     - Function that takes a set of the x-data and a set of parameters
                    and returns a model of the y data.
                        ex. y_guess = model(x, p_guess), for some set of parameters, p_guess.
                    model should be a function, class method, or other callable object.

        savepath  -  

        cost      - Optional. Function used to determine goodness-of-fit for each set of parameters tested.
                    By default this is set to be the sum of the residuals squared.
        '''
        self.x = np.array(x)
        self.y = np.array(y)
        self.model = model
        self.cost = cost
        self.walkers = []
        self.savepath=savepath
        if type(pnames) != type(None):
            self.pnames = list(pnames)

    def __getitem__(self, i):
        return self.walkers[i]

    def get_run_ids(self):
        return list(set([k for w in self.walkers for k in w.get_runs().keys()]))

    def add_walkers(self, n=1, p0=None, psig=None):
        '''
        Method for adding additional walkers to use during fitting.
        

        n    - Optional. Number of walkers to add. Default it 1.

        p0   - Starting position of the new walkers. Default is to start
               walkers off at the current position of the mcmc, self.p

        psig - Starting stepsize of the new walkers. Default is to start
               walkers off at the current position of the mcmc, self.p
        '''

        if type(p0)==type(None):
            try:
                p0 = self.walkers[-1].p
            except IndexError:
                raise ValueError("Could not determine initial set of parameters. p0 must be given for the first walker.")
        if type(psig)==type(None):
            try:
                psig = self.walkers[-1].psig
            except IndexError:
                pass

        for i in range(n):
            self.walkers.append(walker(self.x, self.y, self.model, p0, psig, self.cost))

    def move_to_best_walker(self, **kwargs):
        best = np.array([w.get_best_p(**kwargs) for w in self.walkers])
        best_params = best[:,1:]
        best_costs = best[:,0]
        best_wi = np.argmin(best_costs)
        for wi in range(len(self.walkers)):
            self.walkers[wi].p = best_params[best_wi]
            self.walkers[wi].psig = self.walkers[best_wi].psig.copy()
        self.p = best_params[best_wi]

    def save_walker_history(self, savepath=None, run_ids=None):
        if savepath == None:
            savepath=self.savepath
        if savepath == None:
            raise ValueError("Save path not specified.")

        #Check if run_id is a list or a single string
        #Make sure it's iterable.
        try:
            assert type(run_ids) == str
            run_ids = [run_ids]
        except AssertionError:
            pass
        try:
            iter(run_ids)
        except:
            run_ids = self.get_run_ids()
        
        for run_id in run_ids:
            for wi in range(len(self.walkers)):
                fmt = "%.5e" #["%d"]+["%.5e"]*(len(save_arr[0])-1)
                np.savetxt(savepath+"/"+run_id+"_"+str(wi)+".dat", self.walkers[wi].runs[run_id], fmt=fmt)
    
    def burn(self, nsteps):
        '''
        Method to run a burn stage. All walkers burn for the given number of steps.

        nsteps - Number of steps in the burn stage.
        '''
        for w in self.walkers:
            w.burn(nsteps)

    def check_convergence(self, tol):
        walker_means = np.vstack([w.get_mean() for w in self.walkers])
        mean = np.mean(walker_means, axis=0)
        #print np.max(np.abs(walker_means - mean), axis=0)
        #print mean, np.std(walker_means, axis=0)
        return np.all( np.abs(walker_means - mean) < tol )

    def walk(self, nsteps, wi='all', run_id=None, save=True):#tol, min_nsteps=1000, max_nsteps=2000):
        if type(wi)==str and wi=='all':
            wi = range(len(self.walkers))
        for n in range(nsteps):
            #Walk
            for i in wi:
                self.walkers[i].walk(1, run_id=run_id)

            #Update walker history.
            if save and self.savepath != None and n%10==0:
                self.save_walker_history(run_ids=run_id)

    def get_p_accepted(self, run_id):
        return np.vstack([w.runs[run_id] for w in self.walkers if run_id in w.runs])

    def plot_accepted(self, run_id):
        axes = [plt.subplots()[1] for n in range(int(comb(len(self.walkers[0].p),2)))]
        for i,w in enumerate(self.walkers):
            w.plot_accepted(run_id, axes=axes, label="Walker #"+str(i+1))

    def plot_fit(self):
        fig, ax = plt.subplots()
        ax.scatter(self.x, self.y, color='blue')
        ax.plot(self.x, self.model(self.x, self.p), color='red')

    def plot_sample(self, run_id, n):
        fig, ax = plt.subplots()
        p_accepted = self.get_p_accepted(run_id)[:,1:]
        for i in np.random.choice(len(p_accepted), n):
            ax.plot(self.x, self.model(self.x, p_accepted[i]))
        ax.scatter(self.x, self.y)

    def load_walker_history(self, path):
        '''
        Function for loading walkers from previously saved walker histories.
        
        path - Path to the .mpac file to be loaded.
        '''
        walker_runs = {}
        for fpath in glob.glob(path+"/*.dat"):
            run_id, wi = (fpath.split("/")[-1])[:-4].split("_")
            if not wi in walker_runs:
                walker_runs[wi] = {}
            walker_runs[wi][run_id] = np.loadtxt(fpath)

        for k in sorted(walker_runs.keys(),key=lambda s: int(s)):
            sample_p = walker_runs[k].values()[0][0][1:]
            w = walker(self.x, self.y, self.model, np.ones_like(sample_p))
            w.runs = walker_runs[k]
            self.walkers.append(w)


class walker:
    def __init__(self, x, y, model, p0, psig=None, cost=None):
        self.x = np.array(x)
        self.y = np.array(y)
        self.model = model

        #Define a cost function.
        if cost == None:
            self.cost = lambda p, x=x, y=y, m=model: np.sum(( m(x, p) - y)**2)
        else:
            self.cost = cost

        if type(psig)==type(None):
            self.psig=np.ones_like(p0)
        else:
            self.psig=np.array(psig).copy()

        self.move_to_p(p0)

        self.runs = {}

        self.accept_sample = [[] for a in range(len(self.p))]
        self.n_sample = 25

    def get_current_p(self):
        '''
        Return the current set of parameters that this walker sits at.
        '''
        return self.p

    def get_best_p(self, run_id, method="mean"):
        if method=="mean":
            return np.mean(self.runs[run_id], axis=0)
        if method=="recent":
            return self.runs[run_id][-1]

    def get_runs(self):
        return self.runs

    def move_to_p(self, p, p_cost=None):
        self.p = np.array(p).copy()
        if p_cost==None:
            self.c = self.cost(self.p)
        else:
            self.c = float(p_cost)

    def step(self, run_id=None):
        '''
        Pick a new prospective set of parameters and see how closely they fit the data.
        If it passes the acceptance criterion, move to these parameters.
        Otherwise, do nothing.
        '''
        p_order = np.random.choice(len(self.p), len(self.p), replace=False)
        cprosp_arr = []
        currc_arr = []
        lrat_arr = []
        lrat_cond_arr = []
        accrej_arr = []
        orig_p = self.p.copy()
        p_prosp = self.p.copy()

        #Step in all parameters individually one at a time.
        for i in p_order:
            p_prospective = self.p.copy()
            p_prospective[i] += np.random.normal(0, self.psig[i])
            p_prosp[i] = float(p_prospective[i])
            
            c_prosp = self.cost(p_prospective)
            likelihood_ratio = np.exp((-c_prosp + self.c))

            cond = np.random.uniform(0,1)
            if likelihood_ratio > cond:
                # New paramter was accepted
                self.move_to_p(p_prospective, c_prosp)
                self.accept_sample[i].append(1) # 1 for accepted steps
            else:
                #print "REJECTED"
                # New paramter was rejected
                self.accept_sample[i].append(0) # 0 for rejected steps
                accrej_arr.append(0)
                self.p[i] = float(orig_p[i])

            # Update psig[i] value so that ~50% of steps are accepted.
            if len(self.accept_sample[i]) >= self.n_sample:
                if np.sum(self.accept_sample[i]) > 0:
                    self.psig[i] *= np.sum(self.accept_sample[i])/float(len(self.accept_sample[i])) / 0.5
                else:
                    self.psig[i] /= 2.0
                self.accept_sample[i] = []

        #Ensure that stored value for cost is correct.
        self.c = self.cost(self.p)

        if run_id!=None:
            if not run_id in self.runs:
                self.runs[run_id] = np.array( [np.insert(self.p, 0, self.c)] ) 
            else:
                self.runs[run_id] = np.vstack(( self.runs[run_id] , np.insert(self.p, 0, self.c) ))

    def walk(self, nsteps, run_id=None):
        if run_id==None:
            n=0
            while "walk"+str(n) in self.runs:
                n+=1
            run_id = "walk"+str(n)

        for i in range(nsteps):
            self.step(run_id)

    def get_mean(self):
        return np.mean(self.runs, axis=0)

    def plot_accepted(self, run_id, axes=None, **kwargs):
        params = self.runs[run_id][:,1:].T
        p_combs = list(combinations(params, 2))
        axis_labels = list(combinations(range(1,len(params)+1), 2))
        if type(axes) == type(None) or len(axes) != len(p_combs):
            axes = [plt.subplots()[1] for n in range(len(self.p))]

        for i in range(len(p_combs)):
            p1 = p_combs[i][0]
            p2 = p_combs[i][1]
            axes[i].set_xlabel('Q'+str(axis_labels[i][0]))
            axes[i].set_ylabel('Q'+str(axis_labels[i][1]))
            axes[i].scatter(p1, p2, **kwargs)
            axes[i].legend()
