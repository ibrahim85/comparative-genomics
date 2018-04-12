#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2014 David Emms
#
# This program (OrthoFinder) is distributed under the terms of the GNU General Public License v3
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#  
#  When publishing work that uses OrthoFinder please cite:
#      Emms, D.M. and Kelly, S. (2015) OrthoFinder: solving fundamental biases in whole genome comparisons dramatically 
#      improves orthogroup inference accuracy, Genome Biology 16:157
#
# For any enquiries send an email to David Emms
# david_emms@hotmail.comhor: david

import os
import sys
import csv
import time
import shutil
import numpy as np
import subprocess
from collections import Counter, defaultdict
import itertools
import multiprocessing as mp
import Queue
import warnings

import util
import tree
import matrices
import mcl as MCL
import stride
import trees2ologs_dlcpar
import trees2ologs_of
import blast_file_processor as BlastFileProcessor
import trees_msa
import wrapper_phyldog
import stag
import files

nThreads = util.nThreadsDefault

# Fix LD_LIBRARY_PATH when using pyinstaller 
my_env = os.environ.copy()
if getattr(sys, 'frozen', False):
    if 'LD_LIBRARY_PATH_ORIG' in my_env:
        my_env['LD_LIBRARY_PATH'] = my_env['LD_LIBRARY_PATH_ORIG']  
    else:
        my_env['LD_LIBRARY_PATH'] = ''  
    if 'DYLD_LIBRARY_PATH_ORIG' in my_env:
        my_env['DYLD_LIBRARY_PATH'] = my_env['DYLD_LIBRARY_PATH_ORIG']  
    else:
        my_env['DYLD_LIBRARY_PATH'] = ''     
    
class Seq(object):
    def __init__(self, seqInput):
        """ Constructor takes sequence in any format and returns generators the 
        Seq object accordingly. If performance is really important then can write 
        individual an @classmethod to do that without the checks"""
        if type(seqInput) is str:
            self.iSp, self.iSeq = map(int, seqInput.split("_"))
        elif len(seqInput) == 2:
            if seqInput[0] is str:
                self.iSp, self.iSeq = map(int, seqInput)
            else:
                self.iSp= seqInput[0]
                self.iSeq = seqInput[1]
        else:
            raise NotImplemented
    
    def __eq__(self, other):
        return (isinstance(other, self.__class__)
            and self.__dict__ == other.__dict__)

    def __ne__(self, other):
        return not self.__eq__(other)         
        
    def __repr__(self):
        return self.ToString()
    
    def ToString(self):
        return "%d_%d" % (self.iSp, self.iSeq)

# ==============================================================================================================================
        
class OrthoGroupsSet(object):
    def __init__(self, orthofinderWorkingDir, speciesToUse, nSpAll, clustersFilename_pairs, idExtractor = util.FirstWordExtractor):
        self.speciesIDsEx = util.FullAccession(files.FileHandler.GetSpeciesIDsFN())
        self._Spec_SeqIDs = None
        self._extractor = idExtractor
        self.clustersFN = clustersFilename_pairs
        self.seqIDsEx = None
        self.ogs_all = None
        self.iOgs4 = 0
        self.speciesToUse = speciesToUse
        self.seqsInfo = util.GetSeqsInfo(orthofinderWorkingDir, self.speciesToUse, nSpAll)
        self.fileInfo = util.FileInfo(workingDir = orthofinderWorkingDir, graphFilename="")
        self.id_to_og = None

    def SequenceDict(self):
        if self.seqIDsEx == None:
            try:
                self.seqIDsEx = self._extractor(files.FileHandler.GetSequenceIDsFN())
            except RuntimeError as error:
                print(error.message)
                if error.message.startswith("ERROR"): 
                    util.Fail()
                else:
                    print("Tried to use only the first part of the accession in order to list the sequences in each orthogroup\nmore concisely but these were not unique. The full accession line will be used instead.\n")     
                    self.seqIDsEx = util.FullAccession(files.FileHandler.GetSequenceIDsFN())
        return self.seqIDsEx.GetIDToNameDict()
        
    def SpeciesDict(self):
        d = self.speciesIDsEx.GetIDToNameDict()
        return {k:v.rsplit(".",1)[0] for k,v in d.items()}
        
    def Spec_SeqDict(self):
        if self._Spec_SeqIDs != None:
            return self._Spec_SeqIDs
        seqs = self.SequenceDict()
        specs = self.SpeciesDict()
        specs_ed = {k:v.replace(".", "_").replace(" ", "_") for k,v in specs.items()}
        self._Spec_SeqIDs = {seqID:specs_ed[seqID.split("_")[0]] + "_" + name for seqID, name in seqs.items()}
        return self._Spec_SeqIDs
    
    def OGs(self, qInclAll=False):
        if self.ogs_all != None:
            if qInclAll:
                return self.ogs_all
            else:
                return self.ogs_all[:self.iOgs4]
        ogs = MCL.GetPredictedOGs(self.clustersFN)     
        self.ogs_all = [[Seq(g) for g in og] for og in ogs]   
        self.iOgs4 = len(self.ogs_all) if len(self.ogs_all[-1]) >= 4 else next(i for i, og in enumerate(self.ogs_all) if len(og) < 4) 
        if qInclAll:
            return self.ogs_all
        else:
            return self.ogs_all[:self.iOgs4]
        
    def OrthogroupMatrix(self):
        """ qReduce give a matrix with only as many columns as species for cases when 
        clustering has been performed on a subset of species"""
        ogs = self.OGs()
        iSpecies = sorted(set([gene.iSp for og in ogs for gene in og]))
        speciesIndexDict = {iSp:iCol for iCol, iSp in enumerate(iSpecies)}
        nSpecies = len(iSpecies)
        nGroups = len(ogs)
        # (i, j)-th entry of ogMatrix gives the number of genes from i in orthologous group j
        ogMatrix = np.zeros((nGroups, nSpecies)) 
        for i_og, og in enumerate(ogs):
            for gene in og:
                ogMatrix[i_og, speciesIndexDict[gene.iSp]] += 1
        return ogMatrix
        
    def ID_to_OG_Dict(self):
        if self.id_to_og != None:
            return self.id_to_og
        self.id_to_og = {g.ToString():iog for iog, og in enumerate(self.OGs()) for g in og}
        return self.id_to_og
        
# ==============================================================================================================================

def lil_min(M):
    n = M.shape[0]
    mins = np.ones((n, 1), dtype = np.float64) * 9e99
    for kRow in xrange(n):
        values=M.getrowview(kRow)
        if values.nnz == 0:
            continue
        mins[kRow] = min(values.data[0])
    return mins 

def lil_max(M):
    n = M.shape[0]
    maxes = np.zeros((n, 1), dtype = np.float64)
    for kRow in xrange(n):
        values=M.getrowview(kRow)
        if values.nnz == 0:
            continue
        maxes[kRow] = max(values.data[0])
    return maxes

def lil_minmax(M):
    n = M.shape[0]
    mins = np.ones((n, 1), dtype = np.float64) * 9e99
    maxes = np.zeros((n, 1), dtype = np.float64)
    for kRow in xrange(n):
        values=M.getrowview(kRow)
        if values.nnz == 0:
            continue
        mins[kRow] = min(values.data[0])
        maxes[kRow] = max(values.data[0])
    return mins, maxes
 
   
# ==============================================================================================================================    
# Species trees for two- & three-species analyses

def GetSpeciesTree_TwoThree(taxa):
    """
    Get the unrooted species tree for two or three species
    Args:
        taxa - list of species names
    Returns:
    
    """
    speciesTreeFN_ids = files.FileHandler.GetSpeciesTreeUnrootedFN()
    t = tree.Tree()
    for s in taxa:
        t.add_child(tree.TreeNode(name=s))
    t.write(outfile=speciesTreeFN_ids)
    return speciesTreeFN_ids    
    
def GetSpeciesTreeRoot_TwoTaxa(taxa):
    speciesTreeFN_ids = files.FileHandler.GetSpeciesTreeUnrootedFN()
    t = tree.Tree("(%s,%s);" % (taxa[0], taxa[1]))  
    t.write(outfile=speciesTreeFN_ids)
    return speciesTreeFN_ids
    
# ==============================================================================================================================      
# DendroBlast   

def Worker_OGMatrices_ReadBLASTAndUpdateDistances(cmd_queue, ogMatrices, nGenes, seqsInfo, fileInfo, ogsPerSpecies, qDoubleBlast):
    speciesToUse = seqsInfo.speciesToUse
    with np.errstate(divide='ignore'):
        while True:
            try:
                iiSp, sp1, nSeqs_sp1 = cmd_queue.get(True, 1)
                Bs = [BlastFileProcessor.GetBLAST6Scores(seqsInfo, fileInfo, sp1, sp2, qExcludeSelfHits = False, qDoubleBlast=qDoubleBlast) for sp2 in speciesToUse]
                mins = np.ones((nSeqs_sp1, 1), dtype=np.float64)*9e99 
                maxes = np.zeros((nSeqs_sp1, 1), dtype=np.float64)
                for B in Bs:
                    m0, m1 = lil_minmax(B)
                    mins = np.minimum(mins, m0)
                    maxes = np.maximum(maxes, m1)
                maxes_inv = 1./maxes
                for jjSp, B  in enumerate(Bs):
                    for og, m in zip(ogsPerSpecies, ogMatrices):
                        for gi, i in og[iiSp]:
                            for gj, j in og[jjSp]:
                                    m[i][j] = 0.5*max(B[gi.iSeq, gj.iSeq], mins[gi.iSeq]) * maxes_inv[gi.iSeq]
            except Queue.Empty:
                return 
                
class DendroBLASTTrees(object):
    def __init__(self, ogSet, nProcesses, qDoubleBlast):
        self.ogSet = ogSet
        self.nProcesses = nProcesses
        self.qDoubleBlast = qDoubleBlast
        # Check files exist
    
    def TreeFilename_IDs(self, iog):
        return files.FileHandler.GetOGsTreeFN(iog)
        
    def GetOGMatrices_FullParallel(self):
        """
        read the blast files as well, remove need for intermediate pickle and unpickle
        ogMatrices contains matrix M for each OG where:
            Mij = 0.5*max(Bij, Bmin_i)/Bmax_i
        """
        with warnings.catch_warnings():         
            warnings.simplefilter("ignore")
            ogs = self.ogSet.OGs()
            ogsPerSpecies = [[[(g, i) for i, g in enumerate(og) if g.iSp == iSp] for iSp in self.ogSet.seqsInfo.speciesToUse] for og in ogs]
            nGenes = [len(og) for og in ogs]
            nSeqs = self.ogSet.seqsInfo.nSeqsPerSpecies
            ogMatrices = [[mp.Array('d', n, lock=False) for _ in xrange(n)] for n in nGenes]
            cmd_queue = mp.Queue()
            for iiSp, sp1 in enumerate(self.ogSet.seqsInfo.speciesToUse):
                cmd_queue.put((iiSp, sp1, nSeqs[sp1]))
            runningProcesses = [mp.Process(target=Worker_OGMatrices_ReadBLASTAndUpdateDistances, args=(cmd_queue, ogMatrices, nGenes, self.ogSet.seqsInfo, self.ogSet.fileInfo, ogsPerSpecies, self.qDoubleBlast)) for i_ in xrange(self.nProcesses)]
            for proc in runningProcesses:
                proc.start()
            for proc in runningProcesses:
                while proc.is_alive():
                    proc.join() 
            #ogMatrices = [np.matrix(m) for m in ogMatrices]
            return ogs, ogMatrices      
                   
    def CompleteOGMatrices(self, ogs, ogMatrices):
        newMatrices = []
        for iog, (og, m) in enumerate(zip(ogs, ogMatrices)):
            # dendroblast scores
            n = m.shape[0]
            m2 = np.zeros(m.shape)
            max_og = -9e99
            for i in xrange(n):
                for j in xrange(i):
                    m2[i, j] = -np.log(m[i,j] + m[j,i])  
                    m2[j, i] = m2[i, j]  
                    max_og = max(max_og, m2[i,j])
            newMatrices.append(m2)
        return newMatrices
        
    def CompleteAndWriteOGMatrices(self, ogs, ogMatrices):
        """
        ogMatrices - each matrix is a list of mp.Array  (so that each represents an nSeq x nSeq matrix
        """
        for iog, (og, m) in enumerate(zip(ogs, ogMatrices)):
            # dendroblast scores
            n = len(m)
            max_og = -9e99
            # Careful not to over-write a value and then attempt to try to use the old value
            for i in xrange(n):
                for j in xrange(i):
                    m[i][j] = -np.log(m[i][j] + m[j][i])  
                    m[j][i] = m[i][j]  
                    max_og = max(max_og, m[i][j])
            self.WritePhylipMatrix(m, [g.ToString() for g in og], files.FileHandler.GetOGsDistMatFN(iog), max_og)
        return ogMatrices
    
    @staticmethod
    def WritePhylipMatrix(m, names, outFN, max_og):
        """
        m - list of mp.Array  (so that each represents an nSeq x nSeq matrix
        """
        max_og = 1.1*max_og
        sliver = 1e-6
        with open(outFN, 'wb') as outfile:
            n = len(m)
            outfile.write("%d\n" % n)
            for i in xrange(n):
                outfile.write(names[i] + " ")
                # values could be -inf, these are the most distantly related so replace with max_og
                V = [0. + (m[i][j] if m[i][j] > -9e99 else max_og) for j in range(n)] # "0. +": hack to avoid printing out "-0"
                V = [sliver if 0 < v < sliver  else v for v in V]  # make sure scientific notation is not used (not accepted by fastme)
                values = " ".join(["%.6f" % v for v in V])   
                outfile.write(values + "\n")
    
    def SpeciesTreeDistances(self, ogs, ogMatrices, method = 0):
        """
        ogMatrices - each matrix is a list of mp.Array  (so that each represents an nSeq x nSeq matrix
        """
        spPairs = list(itertools.combinations(self.ogSet.seqsInfo.speciesToUse, 2))
        D = [[] for _ in spPairs]
        if method == 0:
            """ closest distance for each species pair in each orthogroup"""
            for og, m in zip(ogs, ogMatrices):
                spDict = defaultdict(list)
                for i, g in enumerate(og):
                    spDict[g.iSp].append(i)
                for (sp1, sp2), d_list in zip(spPairs, D):
                    distances = [m[i][j] for i in spDict[sp1] for j in spDict[sp2]]
                    if len(distances) > 0: d_list.append(min(distances))
#                    d_list.append(min(distances) if len(distances) > 0 else None)
        return D, spPairs
    
    def PrepareSpeciesTreeCommand(self, D, spPairs, qPutInWorkingDir=False):
        n = len(self.ogSet.seqsInfo.speciesToUse)
        M = np.zeros((n, n))
        for (sp1, sp2), d in zip(spPairs, D):
            sp1 = self.ogSet.seqsInfo.speciesToUse.index(sp1)
            sp2 = self.ogSet.seqsInfo.speciesToUse.index(sp2)
            x = np.median(d)
            M[sp1, sp2] = x
            M[sp2, sp1] = x
        speciesMatrixFN = files.FileHandler.GetSpeciesTreeMatrixFN(qPutInWorkingDir)  
        sliver = 1e-6
        with open(speciesMatrixFN, 'wb') as outfile:
            outfile.write("%d\n" % n)
            for i in xrange(n):
                outfile.write(str(self.ogSet.seqsInfo.speciesToUse[i]) + " ")
                V = [(0. + M[i,j]) for j in range(n)]  # hack to avoid printing out "-0"
                V = [sliver if 0 < v < sliver else v for v in V]  # make sure scientific notation is not used (not accepted by fastme)
                values = " ".join(["%.6f" % v for v in V])   
                outfile.write(values + "\n")       
        treeFN = files.FileHandler.GetSpeciesTreeUnrootedFN()
        cmd = " ".join(["fastme", "-i", speciesMatrixFN, "-o", treeFN, "-N", "-w", "O"] + (["-s"] if n < 1000 else []))
        return cmd, treeFN
                
    def PrepareGeneTreeCommand(self):
        cmds = []
        ogs = self.ogSet.OGs()
        for iog in xrange(len(ogs)):
            nTaxa = len(ogs[iog])
            cmds.append([" ".join(["fastme", "-i", files.FileHandler.GetOGsDistMatFN(iog), "-o", files.FileHandler.GetOGsTreeFN(iog), "-N", "-w", "O"] + (["-s"] if nTaxa < 1000 else []))])
        return cmds

    @staticmethod    
    def EnoughOGsForSTAG(ogs, speciesToUse):
        nSp = len(speciesToUse)
        nSp_perOG = [len(set([g.iSp for g in og])) for og in ogs]
        return (nSp_perOG.count(nSp) >= 100)
    
    def RunAnalysis(self, qSpeciesTree=True):
        util.PrintUnderline("Calculating gene distances")
        ogs, ogMatrices_partial = self.GetOGMatrices_FullParallel()
        ogMatrices = self.CompleteAndWriteOGMatrices(ogs, ogMatrices_partial)
        util.PrintTime("Done")
        cmds_trees = self.PrepareGeneTreeCommand()
        qLessThanFourSpecies = len(self.ogSet.seqsInfo.speciesToUse) < 4
        if qLessThanFourSpecies:
            qSTAG = False
            spTreeFN_ids = GetSpeciesTree_TwoThree(self.ogSet.seqsInfo.speciesToUse)
        else:
            qSTAG = self.EnoughOGsForSTAG(ogs, self.ogSet.seqsInfo.speciesToUse)
            if not qSTAG:
                print("Using fallback species tree inference method")
                D, spPairs = self.SpeciesTreeDistances(ogs, ogMatrices)
                cmd_spTree, spTreeFN_ids = self.PrepareSpeciesTreeCommand(D, spPairs)
                cmds_trees = [[cmd_spTree]] + cmds_trees
        util.PrintUnderline("Inferring gene and species trees")
        util.RunParallelOrderedCommandLists(self.nProcesses, cmds_trees, qHideStdout = True)
        if qSTAG:
            # Trees must have been completed
            print("")
            spTreeFN_ids = stag.Run_ForOrthoFinder(files.FileHandler.GetOGsTreeDir(), files.FileHandler.GetWorkingDirectory2(), self.ogSet.seqsInfo.speciesToUse)
        seqDict = self.ogSet.Spec_SeqDict()
        for iog in xrange(len(self.ogSet.OGs())):
            util.RenameTreeTaxa(files.FileHandler.GetOGsTreeFN(iog), files.FileHandler.GetOGsTreeFN(iog, True), seqDict, qSupport=False, qFixNegatives=True)
        if qSpeciesTree:
            spTreeUnrootedFN = files.FileHandler.GetSpeciesTreeUnrootedFN(True) 
            util.RenameTreeTaxa(spTreeFN_ids, spTreeUnrootedFN, self.ogSet.SpeciesDict(), qSupport=False, qFixNegatives=True)        
            return spTreeFN_ids, spTreeUnrootedFN, qSTAG
        else:      
            return None, None, qSTAG
            
    def SpeciesTreeOnly(self):
        qLessThanFourSpecies = len(self.ogSet.seqsInfo.speciesToUse) < 4
        if qLessThanFourSpecies:
            spTreeFN_ids = GetSpeciesTree_TwoThree(self.ogSet.seqsInfo.speciesToUse)
        else:
            ogs, ogMatrices_partial = self.GetOGMatrices_FullParallel()
            ogMatrices = self.CompleteOGMatrices(ogs, ogMatrices_partial)
            D, spPairs = self.SpeciesTreeDistances(ogs, ogMatrices)
            cmd_spTree, spTreeFN_ids = self.PrepareSpeciesTreeCommand(D, spPairs, True)
            util.RunOrderedCommandList([cmd_spTree], True, True)
        spTreeUnrootedFN = files.FileHandler.GetSpeciesTreeUnrootedFN(True) 
        util.RenameTreeTaxa(spTreeFN_ids, spTreeUnrootedFN, self.ogSet.SpeciesDict(), qSupport=False, qFixNegatives=True)  
        return spTreeFN_ids, spTreeUnrootedFN


# ==============================================================================================================================      
# DLCPar

def GetTotalLength(t):
    return sum([node.dist for node in t])
  
def AllEqualBranchLengths(t):
    lengths = [node.dist for node in t]
    return (len(lengths) > 1 and len(set(lengths)) == 1)

def RootGeneTreesArbitrarily(nOGs, outputDir):
    filenames = [files.FileHandler.GetOGsTreeFN(i) for i in xrange(nOGs)]
    outFilenames = [outputDir + os.path.split(files.FileHandler.GetOGsTreeFN(i))[1] for i in xrange(nOGs)]
    treeFilenames = [fn for fn in filenames if fn.endswith(".txt")]
    nErrors = 0
    with open(outputDir + 'root_errors.txt', 'wb') as errorfile:
        for treeFN, outFN in zip(treeFilenames, outFilenames):
            try:    
                t = tree.Tree(treeFN)
                if len(t.get_children()) != 2:
                    R = t.get_midpoint_outgroup()
                    # if it's a tree with 3 genes all with zero length branches then root arbitrarily (it's possible this could happen with more than 3 nodes)
                    if GetTotalLength(t) == 0.0:
                      for leaf in t:
                        R = leaf
                        break
                    elif AllEqualBranchLengths(t):
                      # more generally, for any branch length all branches could have that same length
                      for leaf in t:
                        R = leaf
                        break
                    t.set_outgroup(R)
                t.resolve_polytomy()
                t.write(outfile = outFN)
            except Exception as err:
                try:
                    t = tree.Tree(treeFN)
                    for leaf in t:
                       R = leaf
                       break
                    t.set_outgroup(R)
                    t.resolve_polytomy()
                    t.write(outfile = outFN)
                except:
                    errorfile.write(treeFN + ": " + str(err) + '\n')
                    nErrors += 1    
    if nErrors != 0:
      print("WARNING: Some trees could not be rooted")
      print("Usually this is because the tree contains genes from a single species.")    

def WriteGeneSpeciesMap(d, speciesDict):
    fn = d + "GeneMap.smap"
    iSpecies = speciesDict.keys()
    with open(fn, 'wb') as outfile:
        for iSp in iSpecies:
            outfile.write("%s_*\t%s\n" % (iSp, iSp))
    return fn

def RunDlcpar(ogSet, speciesTreeFN, workingDir, nParallel, qDeepSearch):
    """
    
    Implementation:
    - (skip: label species tree)
    - sort out trees (midpoint root, resolve plytomies etc)
    - run
    
    """
    ogs = ogSet.OGs()
    nOGs = len(ogs)
    dlcparResultsDir = workingDir + 'dlcpar/'
    if not os.path.exists(dlcparResultsDir): os.mkdir(dlcparResultsDir)
    RootGeneTreesArbitrarily(nOGs, dlcparResultsDir)
    geneMapFN = WriteGeneSpeciesMap(dlcparResultsDir, ogSet.SpeciesDict())
    filenames = [dlcparResultsDir + os.path.split(files.FileHandler.GetOGsTreeFN(i))[1] for i in xrange(nOGs)]
    if qDeepSearch:
        nTaxa = [len(og) for og in ogs[:nOGs]]
        nIter =     [1000 if n < 25 else 25000 if n < 200 else 50000 for n in nTaxa]
        nNoImprov = [ 100 if n < 25 else  1000 if n < 200 else  2000 for n in nTaxa]
        dlcCommands = ['dlcpar_search -s %s -S %s -D 1 -C 0.125 %s -I .txt -i %d --nprescreen 100 --nconverge %d' % (speciesTreeFN, geneMapFN, fn, i, n) for (fn, i, n) in zip(filenames, nIter, nNoImprov)]
    else:
        dlcCommands = ['dlcpar_search -s %s -S %s -D 1 -C 0.125 %s -I .txt -x 1' % (speciesTreeFN, geneMapFN, fn) for fn in filenames]
    util.RunParallelOrderedCommandLists(nParallel, [[c] for c in dlcCommands], qHideStdout = True)
    return dlcparResultsDir

# ==============================================================================================================================      
# Main
            
def CheckUserSpeciesTree(speciesTreeFN, expSpecies):
    # File exists
    if not os.path.exists(speciesTreeFN):
        print("Species tree file does not exist: %s" % speciesTreeFN)
        util.Fail()
    # Species in tree are unique
    try:
        t = tree.Tree(speciesTreeFN, format=1)
    except Exception as e:
        print("\nERROR: Incorrectly formated user-supplied species tree")
        print(e.message)
        util.Fail()
    actSpecies = (t.get_leaf_names())
    c = Counter(actSpecies)
    if 1 != c.most_common()[0][1]:
        print("ERROR: Species names in species tree are not unique")
        for sp, n in c.most_common():
            if 1 != n:
                print("Species '%s' appears %d times" % (sp, n))
        util.Fail()
    # All required species are present
    actSpecies = set(actSpecies)
    ok = True
    for sp in expSpecies:
        if sp not in actSpecies:
            print("ERROR: '%s' is missing from species tree" % sp)
            ok = False
    # expected species are unique
    c = Counter(expSpecies)
    if 1 != c.most_common()[0][1]:
        print("ERROR: Species names are not unique")
        for sp, n in c.most_common():
            if 1 != n:
                print("Species '%s' appears %d times" % (sp, n))
        util.Fail()
    expSpecies = set(expSpecies)
    for sp in actSpecies:
        if sp not in expSpecies:
            print("ERROR: Additional species '%s' in species tree" % sp)
            ok = False
    if not ok: util.Fail()
    # Tree is rooted
    if len(t.get_children()) != 2:
        print("ERROR: Species tree is not rooted")
        util.Fail()

def ConvertUserSpeciesTree(speciesTreeFN_in, speciesDict, speciesTreeFN_out):
    t = tree.Tree(speciesTreeFN_in, format=1)  
    t.prune(t.get_leaf_names())
    revDict = {v:k for k,v in speciesDict.items()}
    for sp in t:
        sp.name = revDict[sp.name]       
    t.write(outfile=speciesTreeFN_out)
    
def WriteTestDistancesFile(testFN):
    with open(testFN, 'wb') as outfile:
        outfile.write("4\n1_1 0 0 0.2 0.25\n0_2 0 0 0.21 0.28\n3_1 0.2 0.21 0 0\n4_1 0.25 0.28 0 0")
    return testFN

def CanRunOrthologueDependencies(workingDir, qMSAGeneTrees, qPhyldog, qStopAfterTrees, msa_method, tree_method, recon_method, program_caller, qStopAfterAlignments):  
    # FastME
    if (not qMSAGeneTrees):
        testFN = workingDir + "SimpleTest.phy"
        WriteTestDistancesFile(testFN)
        outFN = workingDir + "SimpleTest.tre"
        if os.path.exists(outFN): os.remove(outFN)        
        if not util.CanRunCommand("fastme -i %s -o %s" % (testFN, outFN), qAllowStderr=False):
            print("ERROR: Cannot run fastme")
            print("Please check FastME is installed and that the executables are in the system path.\n")
            return False
        os.remove(testFN)
        os.remove(outFN)
        fastme_stat_fn = workingDir + "SimpleTest.phy_fastme_stat.txt"
        if os.path.exists(fastme_stat_fn): os.remove(fastme_stat_fn)
    # DLCPar
    if ("dlcpar" in recon_method) and not (qStopAfterTrees or qStopAfterAlignments):
        if not util.CanRunCommand("dlcpar_search --version", qAllowStderr=False):
            print("ERROR: Cannot run dlcpar_search")
            print("Please check DLCpar is installed and that the executables are in the system path.\n")
            return False
        if recon_method == "dlcpar_deepsearch":
            capture = subprocess.Popen("dlcpar_search --version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=my_env)
            stdout = "".join([x for x in capture.stdout])
            version = stdout.split()[-1]
            major, minor, release = map(int, version.split("."))
            # require 1.0.1 or above            
            actual = (major, minor, release)
            required = [1,0,1]
            versionOK = True
            for r, a in zip(required, actual):
                if a > r:
                    versionOK = True
                    break
                elif a < r:
                    versionOK = False
                    break
                else:
                    pass
                    # need to check next level down
            if not versionOK:
                print("ERROR: dlcpar_deepsearch requires dlcpar_search version 1.0.1 or above")
                return False                   
    
    # FastTree & MAFFT
    if qMSAGeneTrees or qPhyldog:
        testFN, temp_dir = trees_msa.WriteTestFile(workingDir)
        if msa_method == "mafft":
            if not util.CanRunCommand("mafft %s" % testFN, qAllowStderr=True):
                print("ERROR: Cannot run mafft")
                print("Please check MAFFT is installed and that the executables are in the system path\n")
                return False
        elif msa_method != None:
            if not program_caller.TestMSAMethod(temp_dir, msa_method):
                print("ERROR: Cannot run user-configured MSA method '%s'" % msa_method)
                print("Please check program is installed and that it is correctly configured in the orthofinder/config.json file\n")
                return False
        if tree_method == "fasttree":
            if qMSAGeneTrees and (not qStopAfterAlignments) and not util.CanRunCommand("FastTree %s" % testFN, qAllowStderr=True):
                print("ERROR: Cannot run FastTree")
                print("Please check FastTree is installed and that the executables are in the system path\n")
                return False      
        elif tree_method != None:
            if not program_caller.TestTreeMethod(temp_dir, tree_method):
                print("ERROR: Cannot run user-configured tree method '%s'" % tree_method)
                print("Please check program is installed and that it is correctly configured in the orthofinder/config.json file\n")
                return False
        try:
            shutil.rmtree(temp_dir)
        except OSError:
            time.sleep(1)
            shutil.rmtree(temp_dir, True)  # shutil / NFS bug - ignore errors, it's less crucial that the files are deleted
            
    if qPhyldog:
        if not util.CanRunCommand("mpirun -np 1 phyldog", qAllowStderr=False):
            print("ERROR: Cannot run mpirun -np 1 phyldog")
            print("Please check phyldog is installed and that the executable is in the system path\n")
            return False
        
    return True    
        
def PrintHelp():
    print("Usage")    
    print("-----")
    print("orthologues.py orthofinder_results_directory [-t max_number_of_threads]")
    print("orthologues.py -h")
    print("\n")
    
    print("Arguments")
    print("---------")
    print("""orthofinder_results_directory
    Generate gene trees for the orthogroups, generated rooted species tree and infer ortholgues.\n""")
    
    print("""-t max_number_of_threads, --threads max_number_of_threads
    The maximum number of processes to be run simultaneously. The deafult is %d but this 
    should be increased by the user to the maximum number of cores available.\n""" % util.nThreadsDefault)
        
    print("""-h, --help
   Print this help text""")
    util.PrintCitation()   

def GetResultsFilesString(rootedSpeciesTreeFN, seqs_alignments_dirs=None, qHaveOrthologues=True):
    """uses species tree directory to infer position of remaining files
    """
    st = ""
    baseResultsDir = os.path.abspath(os.path.split(rootedSpeciesTreeFN[0])[0])
    if seqs_alignments_dirs != None:
        st += "\nSequences for orthogroups:\n   %s\n" % seqs_alignments_dirs[0]
        st += "\nMultiple sequence alignments:\n   %s\n" % seqs_alignments_dirs[1]
    st += "\nGene trees:\n   %s\n" % (baseResultsDir + "/Gene_Trees/")
    if len(rootedSpeciesTreeFN) == 1:
#        resultsDir = os.path.split(baseResultsDir[0])[0] + "/Orthologues"
        st += "\nRooted species tree:\n   %s\n" % rootedSpeciesTreeFN[0]
        if qHaveOrthologues: st += "\nSpecies-by-species orthologues:\n   %s\n" % (baseResultsDir + "/Orthologues/")
    else:
        st += "\nWARNING:\n"
        st += "   Multiple potential outgroups were identified for the species tree. Each case has been analysed separately.\n" 
        st+=  "   Please review the rooted species trees and use the results corresponding to the correct one.\n\n"        
        if qHaveOrthologues: 
            for tFN in rootedSpeciesTreeFN:
                resultsDir = os.path.split(tFN)[0] + "/"
                st += "Rooted species tree:\n   %s\n" % tFN
                st += "Species-by-species orthologues directory:\n   %s\n\n" % resultsDir
        else:
            st += "Rooted species trees:\n"
            for tFN in rootedSpeciesTreeFN:
                st += "   %s\n" % tFN
    return st
            
def WriteOrthologuesMatrix(fn, matrix, speciesToUse, speciesDict):
    with open(fn, 'wb') as outfile:
        writer = csv.writer(outfile, delimiter="\t")
        writer.writerow([""] + [speciesDict[str(index)] for index in speciesToUse])
        for ii, iSp in enumerate(speciesToUse):
            overlap = [matrix[ii, jj] for jj, jSp in enumerate(speciesToUse)]
            writer.writerow([speciesDict[str(iSp)]] + overlap)   
    

def WriteOrthologuesStats(ogSet, nOrtho_sp, resultsDir):
    """
    nOrtho_sp is a util.nOrtho_sp object
    """
    speciesToUse = ogSet.speciesToUse
    speciesDict = ogSet.SpeciesDict()
    WriteOrthologuesMatrix(resultsDir + "../OrthologuesStats_Totals.csv", nOrtho_sp.n, speciesToUse, speciesDict)
    WriteOrthologuesMatrix(resultsDir + "../OrthologuesStats_one-to-one.csv", nOrtho_sp.n_121, speciesToUse, speciesDict)
    WriteOrthologuesMatrix(resultsDir + "../OrthologuesStats_one-to-many.csv", nOrtho_sp.n_12m, speciesToUse, speciesDict)
    WriteOrthologuesMatrix(resultsDir + "../OrthologuesStats_many-to-one.csv", nOrtho_sp.n_m21, speciesToUse, speciesDict)
    WriteOrthologuesMatrix(resultsDir + "../OrthologuesStats_many-to-many.csv", nOrtho_sp.n_m2m, speciesToUse, speciesDict)

def TwoAndThreeGeneOrthogroups(ogSet, resultsDir):
    speciesDict = ogSet.SpeciesDict()
    sequenceDict = ogSet.SequenceDict()
    ogs = ogSet.OGs(qInclAll=True)
    nOrthologues_SpPair = util.nOrtho_sp(len(ogSet.speciesToUse))
    all_orthologues = []
    d_empty = defaultdict(list)
    for iog, og in enumerate(ogs):
        n = len(og) 
        if n == 1: break
        elif n == 2:
            if og[0].iSp == og[1].iSp: continue
            # orthologues is a list of tuples of dictionaries
            # each dictionary is sp->list of genes in species
            d0 = defaultdict(list)
            d0[str(og[0].iSp)].append(str(og[0].iSeq))
            d1 = defaultdict(list)
            d1[str(og[1].iSp)].append(str(og[1].iSeq))
            orthologues = [(d0, d1, d_empty, d_empty)]  
        elif n == 3:
            sp = [g.iSp for g in og]
            c = Counter(sp) 
            nSp = len(c)
            if nSp == 3:
                g = [(str(g.iSp), str(g.iSeq)) for g in og]
                d0 = defaultdict(list)
                d0[g[0][0]].append(g[0][1])
                d1 = defaultdict(list)
                d1[g[1][0]].append(g[1][1])
                d1[g[2][0]].append(g[2][1])
                orthologues = [(d0, d1, d_empty, d_empty)]  
                d0 = defaultdict(list)
                d0[g[1][0]].append(g[1][1])
                d1 = defaultdict(list)
                d1[g[2][0]].append(g[2][1])
                orthologues.append((d0,d1, d_empty, d_empty))
            elif nSp == 2:             
                sp0, sp1 = c.keys()
                d0 = defaultdict(list)
                d0[str(sp0)] = [str(g.iSeq) for g in og if g.iSp == sp0]
                d1 = defaultdict(list)
                d1[str(sp1)] = [str(g.iSeq) for g in og if g.iSp == sp1]
                orthologues = [(d0, d1, d_empty, d_empty)]
            else: 
                continue # no orthologues
        elif n >= 4:
            continue
        all_orthologues.append((iog, orthologues))
    nOrthologues_SpPair += trees2ologs_of.AppendOrthologuesToFiles(all_orthologues, speciesDict, ogSet.speciesToUse, sequenceDict, resultsDir, False)
    return nOrthologues_SpPair
    
def ReconciliationAndOrthologues(recon_method, ogSet, speciesTree_fn, resultsDir, reconTreesRenamedDir, nParallel, iSpeciesTree=None, all_stride_dup_genes=None):
    """
    ogSet - info about the orthogroups, species etc
    speciesTree_fn - the species tree
    resultsDir - where the Orthologues top level results directory will go (should exist already)
    reconTreesRenamedDir - where to put the reconcilled trees that use the gene accessions
    iSpeciesTree - which of the potential roots of the species tree is this
    method - can be dlcpar, dlcpar_deep, of_recon
    """
    workingDir = files.FileHandler.GetWorkingDirectory2()    # workingDir - Orthologues working dir
    if not os.path.exists(reconTreesRenamedDir): os.mkdir(reconTreesRenamedDir)
    if "dlcpar" in recon_method:
        qDeepSearch = (recon_method == "dlcpar_deepsearch")
        util.PrintTime("Starting DLCpar")
        dlcparResultsDir = RunDlcpar(ogSet, speciesTree_fn, workingDir, nParallel, qDeepSearch)
        util.PrintTime("Done DLCpar")
        for iog in xrange(len(ogSet.OGs())):
            util.RenameTreeTaxa(dlcparResultsDir + "OG%07d_tree_id.dlcpar.locus.tree" % iog, reconTreesRenamedDir + "OG%07d_tree.txt" % iog, ogSet.Spec_SeqDict(), qSupport=False, qFixNegatives=False, inFormat=8, label='n')
    
        # Orthologue lists
        util.PrintUnderline("Inferring orthologues from gene trees" + (" (root %d)"%iSpeciesTree if iSpeciesTree != None else ""))
        pickleDir = files.FileHandler.GetPickleDir()
        nOrthologues_SpPair = trees2ologs_dlcpar.create_orthologue_lists(ogSet, resultsDir, dlcparResultsDir, pickleDir)  

    elif "phyldog" == recon_method:
        util.PrintTime("Starting Orthologues from Phyldog")
        nOrthologues_SpPair = trees2ologs_of.DoOrthologuesForOrthoFinder_Phyldog(ogSet, workingDir, trees2ologs_of.GeneToSpecies_dash, workingDir, resultsDir, reconTreesRenamedDir)
        util.PrintTime("Done Orthologues from Phyldog")
    else:
        util.PrintTime("Starting OF Orthologues")
        nOrthologues_SpPair = trees2ologs_of.DoOrthologuesForOrthoFinder(ogSet, speciesTree_fn, trees2ologs_of.GeneToSpecies_dash, workingDir, resultsDir, reconTreesRenamedDir, all_stride_dup_genes)
        util.PrintTime("Done OF Orthologues")
    nOrthologues_SpPair += TwoAndThreeGeneOrthogroups(ogSet, resultsDir)
    WriteOrthologuesStats(ogSet, nOrthologues_SpPair, resultsDir)
#    print("Identified %d orthologues" % nOrthologues)
        
                
def OrthologuesFromTrees(recon_method, nHighParallel, userSpeciesTree_fn):
    """
    userSpeciesTree_fn - None if not supplied otherwise rooted tree using user species names (not orthofinder IDs)
    qUserSpTree - is the speciesTree_fn user-supplied
    
    Just infer orthologues from trees, don't do any of the preceeding steps.
    """
#    # Check species tree
#    qUserSpTree = (speciesTree_fn != None)
#    if qUserSpTree:
#        if not os.path.exists(speciesTree_fn):
#            print("\nERROR: %s does not exist\n" % speciesTree_fn)
#            util.Fail()
#    else:
#        possibilities = ["SpeciesTree_ids_0_rooted.txt", "SpeciesTree_ids_1_rooted.txt", "SpeciesTree_user_ids.txt", "SpeciesTree_unrooted_0_rooted.txt"] # etc (only need to determine if unique)
#        nTrees = 0
#        for p in possibilities:
#            for d in [workingDir, workingDir + "Trees_ids/"]:
#                fn = d + p
#                if os.path.exists(fn): 
#                    nTrees += 1
#                    speciesTree_fn = fn
#        if nTrees == 0:
#            print("\nERROR: There is a problem with the specified directory. The rooted species tree %s or %s is not present." % (possibilities[0], possibilities[2]))
#            print("Please rectify the problem or alternatively use the -s option to specify the species tree to use.\n")
#            util.Fail()
#        if nTrees > 1:
#            print("\nERROR: There is more than one rooted species tree in the specified directory structure. Please use the -s option to specify which species tree should be used\n")
#            util.Fail()
#            
#    resultsDir_new = workingDir + "../New_Analysis_From_Trees"      # for the Orthologues_Species/ directories
#    resultsDir_new = util.CreateNewWorkingDirectory(resultsDir_new + "_")
#    resultsDir_new += "Orthologues/"
#    os.mkdir(resultsDir_new)
#    reconTreesRenamedDir = resultsDir_new + "../Recon_Gene_Trees/"
    
    
#        if userSpeciesTree == None:
#            d = util.FullAccession(self.GetSpeciesIDsFN()).GetIDToNameDict()
#            speciesDict = {k:v.rsplit(".",1)[0] for k,v in d.items()}.values()
#            speciesToUseNames = speciesDict.values()
#            CheckUserSpeciesTree(speciesTree_fn, speciesToUseNames)
#            speciesTree_fn = ConvertUserSpeciesTree(speciesTree_fn, speciesDict)

#    orthofinderWorkingDir, orthofinderResultsDir, clustersFilename_pairs = util.GetOGsFile(groupsDir)
    speciesToUse, nSpAll, _ = util.GetSpeciesToUse(files.FileHandler.GetSpeciesIDsFN())    
    ogSet = OrthoGroupsSet(files.FileHandler.GetWorkingDirectory1(), speciesToUse, nSpAll, files.FileHandler.GetClustersFN(), idExtractor = util.FirstWordExtractor)
    if userSpeciesTree_fn != None:
        speciesDict = files.FileHandler.GetSpeciesDict()
        speciesToUseNames = speciesDict.values()
        CheckUserSpeciesTree(userSpeciesTree_fn, speciesToUseNames)
        speciesTreeFN_ids = files.FileHandler.GetWorkingDirectory2() + "SpeciesTree_user_ids_rooted.txt"
        ConvertUserSpeciesTree(userSpeciesTree_fn, speciesDict, speciesTreeFN_ids)
        files.FileHandler.SetSpeciesTreeRootedFN(speciesTreeFN_ids)
    util.PrintUnderline("Running Orthologue Prediction", True)
    util.PrintUnderline("Reconciling gene and species trees") 
    reconTreesRenamedDir = files.FileHandler.GetOGsReconTreeDir(True)   #FileHandlerRemove
    ologsDir_new = files.FileHandler.GetOrthologuesDirectory()
    ReconciliationAndOrthologues(recon_method, ogSet, files.FileHandler.GetSpeciesTreeRootedFN(), ologsDir_new, reconTreesRenamedDir, nHighParallel)
    util.PrintUnderline("Writing results files")
    util.PrintTime("Writing results files")
    files.FileHandler.CleanWorkingDir2()
    return "Species-by-species orthologues directory:\n   %s\n" % ologsDir_new
    
def OrthologuesWorkflow(speciesToUse, nSpAll, 
                       clustersFilename_pairs, 
                       tree_options,
                       msa_method,
                       tree_method,
                       recon_method,
                       nHighParallel,
                       nLowParrallel,
                       qDoubleBlast,
                       userSpeciesTree = None, 
                       qStopAfterSeqs = False,
                       qStopAfterAlign = False,
                       qStopAfterTrees = False, 
                       qMSA = False,
                       qPhyldog = False,
                       results_name = ""):
    """
    1. Setup:
        - ogSet, directories
        - DendroBLASTTress - object
    2. DendrobBLAST:
        - read scores
        - RunAnalysis: Get distance matrices, do trees
    3. Root species tree
    4. Reconciliation/Orthologues
    5. Clean up
    
    Variables:
    - ogSet - all the relevant information about the orthogroups, species etc.
    """
    ogSet = OrthoGroupsSet(files.FileHandler.GetWorkingDirectory1(), speciesToUse, nSpAll, clustersFilename_pairs, idExtractor = util.FirstWordExtractor)
    
    tree_generation_method = "msa" if qMSA or qPhyldog else "dendroblast"
    stop_after = "seqs" if qStopAfterSeqs else "align" if qStopAfterAlign else ""
    files.FileHandler.MakeResultsDirectory2(tree_generation_method, stop_after, results_name)    
#    resultsDir = util.CreateNewWorkingDirectory(files.FileHandler.GetResultsDirectory1() + "Orthologues_" + ("" if results_name == "" else results_name + "_"))
    """ === 1 === ust = UserSpeciesTree
    MSA:               Sequences    Alignments                        GeneTrees    db    SpeciesTree
    Phyldog:           Sequences    Alignments                        GeneTrees    db    SpeciesTree  
    Dendroblast:                                  DistanceMatrices    GeneTrees    db    SpeciesTree
    MSA (ust):         Sequences    Alignments                        GeneTrees    db
    Phyldog (ust):     Sequences    Alignments                        GeneTrees    db      
    Dendroblast (ust):                            DistanceMatrices    GeneTrees    db        
    """
    resultsDir = files.FileHandler.GetResultsDirectory2()
    qDB_SpeciesTree = False
    if qMSA or qPhyldog:
        qLessThanFourSpecies = len(ogSet.seqsInfo.speciesToUse) < 4
        treeGen = trees_msa.TreesForOrthogroups(tree_options, msa_method, tree_method)       
        if (not userSpeciesTree) and qLessThanFourSpecies:
            spTreeFN_ids = GetSpeciesTree_TwoThree(ogSet.seqsInfo.speciesToUse)
            util.RenameTreeTaxa(spTreeFN_ids, files.FileHandler.GetSpeciesTreeUnrootedFN(True), ogSet.SpeciesDict(), qSupport=False, qFixNegatives=True)
        qDoMSASpeciesTree = (not qLessThanFourSpecies) and (not userSpeciesTree)
        util.PrintTime("Starting MSA/Trees")
        seqs_alignments_dirs = treeGen.DoTrees(ogSet.OGs(qInclAll=True), ogSet.OrthogroupMatrix(), ogSet.Spec_SeqDict(), ogSet.SpeciesDict(), nHighParallel, qStopAfterSeqs, qStopAfterAlign or qPhyldog, qDoSpeciesTree=qDoMSASpeciesTree) 
        util.PrintTime("Done MSA/Trees")
        if qDoMSASpeciesTree:
            spTreeFN_ids = files.FileHandler.GetSpeciesTreeUnrootedFN()
        if qStopAfterSeqs:
            print("")
            return ("\nSequences for orthogroups:\n   %s\n" % seqs_alignments_dirs[0])
        elif qStopAfterAlign:
            print("")
            st = "\nSequences for orthogroups:\n   %s\n" % seqs_alignments_dirs[0]
            st += "\nMultiple sequence alignments:\n   %s\n" % seqs_alignments_dirs[1]
            return st
        db = DendroBLASTTrees(ogSet, nLowParrallel, qDoubleBlast)
        if qDB_SpeciesTree and not userSpeciesTree and not qLessThanFourSpecies:
            util.PrintUnderline("Inferring species tree (calculating gene distances)")
            print("Loading BLAST scores")
            spTreeFN_ids, spTreeUnrootedFN = db.SpeciesTreeOnly()
        if qPhyldog:
            util.PrintTime("Starting phyldog")
            species_tree_ids_labelled_phyldog = wrapper_phyldog.RunPhyldogAnalysis(resultsDir + "WorkingDirectory/phyldog/", ogSet.OGs(), speciesToUse, nHighParallel)
    else:
        db = DendroBLASTTrees(ogSet, nLowParrallel, qDoubleBlast)
        spTreeFN_ids, spTreeUnrootedFN, qSTAG = db.RunAnalysis()
    qSpeciesTreeSupports = False if (userSpeciesTree or qMSA or qPhyldog) else qSTAG
    
    """ === 2 ===
    Check can continue with analysis 
    """
#    if len(ogSet.speciesToUse) < 4: 
#        print("ERROR: Not enough species to infer species tree")
#        util.Fail()
     
    """ === 3 ===
    MSA:               RootSpeciesTree
    Phyldog:           RootSpeciesTree    
    Dendroblast:       RootSpeciesTree  
    MSA (ust):         ConvertSpeciesTreeIDs
    Phyldog (ust):     ConvertSpeciesTreeIDs
    Dendroblast (ust): ConvertSpeciesTreeIDs
    """    
    if userSpeciesTree:
        util.PrintUnderline("Using user-supplied species tree") 
        speciesTreeFN_ids = files.FileHandler.GetWorkingDirectory2() + "SpeciesTree_user_ids_rooted.txt"
        ConvertUserSpeciesTree(userSpeciesTree, ogSet.SpeciesDict(), speciesTreeFN_ids)
        rootedSpeciesTreeFN = [speciesTreeFN_ids]
        roots = [None]
        qMultiple = False
        all_stride_dup_genes = None
    elif len(ogSet.seqsInfo.speciesToUse) == 2:
        hardcodeSpeciesTree = GetSpeciesTreeRoot_TwoTaxa(ogSet.seqsInfo.speciesToUse)
        rootedSpeciesTreeFN = [hardcodeSpeciesTree]
        roots = [None]
        qMultiple = False
        all_stride_dup_genes = None
    elif qPhyldog:
        rootedSpeciesTreeFN = [species_tree_ids_labelled_phyldog]
        roots = [None]
        qMultiple = False
        all_stride_dup_genes = None
    else:
        util.PrintUnderline("Best outgroup(s) for species tree") 
        util.PrintTime("Starting STRIDE")
        roots, clusters_counter, rootedSpeciesTreeFN, nSupport, _, _, all_stride_dup_genes = stride.GetRoot(spTreeFN_ids, files.FileHandler.GetOGsTreeDir(), stride.GeneToSpecies_dash, nHighParallel, qWriteRootedTree=True)
        print(rootedSpeciesTreeFN)
        util.PrintTime("Done STRIDE")
        nAll = sum(clusters_counter.values())
        nFP_mp = nAll - nSupport
        n_non_trivial = sum([v for k, v in clusters_counter.items() if len(k) > 1])
        if len(roots) > 1:
            print("Observed %d well-supported, non-terminal duplications. %d support the best roots and %d contradict them." % (n_non_trivial, n_non_trivial-nFP_mp, nFP_mp))
            print("Best outgroups for species tree:")  
        else:
            print("Observed %d well-supported, non-terminal duplications. %d support the best root and %d contradict it." % (n_non_trivial, n_non_trivial-nFP_mp, nFP_mp))
            print("Best outgroup for species tree:")  
        spDict = ogSet.SpeciesDict()
        for r in roots: print("  " + (", ".join([spDict[s] for s in r]))  )
        qMultiple = len(roots) > 1
        
        
    if qStopAfterTrees:
        if userSpeciesTree:
            st = ""
            if qMSA:
                st += "\nSequences for orthogroups:\n   %s\n" % seqs_alignments_dirs[0]
                st += "\nMultiple sequence alignments:\n   %s\n" % seqs_alignments_dirs[1]
            st += "\nGene trees:\n   %s\n" % (resultsDir + "Gene_Trees/")
            return st
        # otherwise, root species tree
        resultsSpeciesTrees = []
        for i, (r, speciesTree_fn) in enumerate(zip(roots, rootedSpeciesTreeFN)):
            if len(roots) == 1:
                resultsSpeciesTrees.append(resultsDir + "SpeciesTree_rooted.txt")
            else:
                resultsSpeciesTrees.append(resultsDir + "SpeciesTree_rooted_at_outgroup_%d.txt" % i)
            labeled_tree_fn = resultsSpeciesTrees[-1][:-4] + "_node_labels.txt"
            
            util.RenameTreeTaxa(speciesTree_fn, resultsSpeciesTrees[-1], db.ogSet.SpeciesDict(), qSupport=qSpeciesTreeSupports, qFixNegatives=True)
            util.RenameTreeTaxa(speciesTree_fn, labeled_tree_fn, db.ogSet.SpeciesDict(), qSupport=False, qFixNegatives=True, label='N')
        files.FileHandler.CleanWorkingDir2()
        return GetResultsFilesString(resultsSpeciesTrees, seqs_alignments_dirs if qMSA else None, False)
    
    if qMultiple: util.PrintUnderline("\nMultiple potential species tree roots were identified, only one will be analyed.", True)
    resultsSpeciesTrees = []
    i = 0
    r = roots[0]
    speciesTree_fn = rootedSpeciesTreeFN[0]
    util.PrintUnderline("Reconciling gene trees and species tree") 
    if userSpeciesTree or qPhyldog or len(ogSet.seqsInfo.speciesToUse) == 2:
        resultsDir_new = resultsDir + "Orthologues/"
        reconTreesRenamedDir = resultsDir + "/Recon_Gene_Trees/"
        resultsSpeciesTrees.append(resultsDir + "SpeciesTree_rooted.txt")
    else:
        resultsDir_new = resultsDir + "Orthologues/"
        reconTreesRenamedDir = resultsDir + "/Recon_Gene_Trees/"
        resultsSpeciesTrees.append(resultsDir + "SpeciesTree_rooted.txt")
        print("Outgroup: " + (", ".join([spDict[s] for s in r])))
    labeled_tree_fn = resultsSpeciesTrees[-1][:-4] + "_node_labels.txt"
    util.RenameTreeTaxa(speciesTree_fn, resultsSpeciesTrees[-1], db.ogSet.SpeciesDict(), qSupport=qSpeciesTreeSupports, qFixNegatives=True)
    util.RenameTreeTaxa(speciesTree_fn, labeled_tree_fn, db.ogSet.SpeciesDict(), qSupport=False, qFixNegatives=True, label='N')
    util.PrintTime("Starting Recon and orthologues")
    ReconciliationAndOrthologues(recon_method, db.ogSet, speciesTree_fn, resultsDir_new, reconTreesRenamedDir, nHighParallel, i if qMultiple else None, all_stride_dup_genes=all_stride_dup_genes) 
    util.PrintTime("Done Recon")
    
    if qMultiple:
        rooted_species_tree_dir = resultsDir + "Potential_Rooted_Species_Trees/"
        os.mkdir(rooted_species_tree_dir)
        for i, (r, speciesTree_fn) in enumerate(zip(roots, rootedSpeciesTreeFN)):
            unanalysedSpeciesTree = rooted_species_tree_dir + "SpeciesTree_rooted_at_outgroup_%d.txt" % i
            util.RenameTreeTaxa(speciesTree_fn, unanalysedSpeciesTree, db.ogSet.SpeciesDict(), qSupport=qSpeciesTreeSupports, qFixNegatives=True, label='N')
    
    files.FileHandler.CleanWorkingDir2()
    util.PrintUnderline("Writing results files", True)
    
    return GetResultsFilesString(resultsSpeciesTrees, seqs_alignments_dirs if qMSA else None)
    
    
    
