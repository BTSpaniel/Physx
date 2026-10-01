// SPDX-License-Identifier: MIT
// First-party numerical revision 3. Revisions 1 and 2 retain their processors.
// Solve the same original-f32-datum complementary-energy problem with
// binary64 geometry, inertia, normalization, force RHS and final conversion. An anchored
// connected component has consistent full-row-rank constraints; invertible row
// whitening preserves its feasible set and minimum Euclidean-norm solution.
// Free components keep the original inertia-weighted least-squares norm and
// use an SPD-on-range right preconditioner with reorthogonalized directions.
// Components iterate independently. No physical mode or pivot is discarded.
#pragma once
#include "pr_blast_section_solver.h"
#include "pr_blast_section_qr.h"
#include <algorithm>
#include <map>
#include <numeric>
#include <queue>
#include <set>

class PrSectionAccurateProcessor : public StressProcessor
{
    using Vec = std::array<double,6>;
    using Mat = std::array<double,36>;
    struct Column { uint32_t node[2]; Mat block[2]; };
    struct LowerEntry { uint32_t column; Mat block; };
    struct Basis { std::vector<Vec> direction, action; };
    struct Component {
        std::vector<uint32_t> nodes, bonds;
        std::vector<Basis> basis;
        double limit=0, error=0;
        uint64_t updates=0;
        bool anchored=false, active=false, qr=false;
    };
    std::vector<Mat> m_factor, m_whitening;
    std::vector<std::vector<LowerEntry>> m_lower;
    std::vector<uint32_t> m_order, m_nodeComponent;
    std::vector<Vec> m_rowScale;
    std::vector<Component> m_components;
    std::map<uint32_t,PrSectionQr> m_qr;
    uint32_t m_failedGram=UINT32_MAX;
    std::vector<Column> m_columns, m_originalColumns;
    double m_preciseMass=1, m_preciseLength=1;
    std::vector<Vec> m_physicalWeights;
    std::vector<Vec> m_x, m_p, m_z, m_rhs64, m_r, m_s, m_nodeWork, m_rowWork;
    bool m_ready = false, m_valid = true, m_solution = false, m_pending = false;
    double m_limit = 0;
    double m_progress[8] = {0,0,0,0,0,0,0,3};
    // Components, supported components, updates(last/total), max/load,
    // retained factor blocks/bytes, current basis bytes, projections(last/total),
    // peak basis bytes, numerical revision. Bytes exclude allocator overhead.
    double m_work[12] = {0,0,0,0,0,0,0,0,0,0,0,3};

    static bool finite(const Vec& value)
    { for (double x : value) if (!std::isfinite(x)) return false; return true; }
    static Vec multiply(const Mat& matrix, const Vec& value, bool transpose = false)
    {
        Vec result{};
        for (unsigned i=0;i<6;++i) for (unsigned j=0;j<6;++j)
            result[i] += matrix[transpose ? j*6+i : i*6+j]*value[j];
        return result;
    }
    static double normSquared(const std::vector<Vec>& values)
    {
        double sum = 0, correction = 0;
        for (const auto& value : values) for (double x : value)
        {
            const double term = x*x-correction;
            const double next = sum+term;
            correction = (next-sum)-term;
            sum = next;
        }
        return sum;
    }
    bool forward(std::vector<Vec>& out, const std::vector<Vec>& in)
    {
        std::fill(out.begin(),out.end(),Vec{});
        for (uint32_t b=0;b<m_columns.size();++b) for (unsigned side=0;side<2;++side)
        {
            const auto& column=m_columns[b];
            const Vec value=multiply(column.block[side],in[b]);
            Vec& node=out[column.node[side]];
            for (unsigned i=0;i<6;++i) node[i]+=value[i];
        }
        for (const auto& value:out) if (!finite(value)) return m_valid=false;
        return true;
    }
    bool transpose(std::vector<Vec>& out, const std::vector<Vec>& in)
    {
        for (uint32_t b=0;b<m_columns.size();++b)
        {
            const auto& column=m_columns[b];
            const Vec a=multiply(column.block[0],in[column.node[0]],true);
            const Vec c=multiply(column.block[1],in[column.node[1]],true);
            for (unsigned i=0;i<6;++i) out[b][i]=a[i]+c[i];
            if (!finite(out[b])) return m_valid=false;
        }
        return true;
    }
    // Original B^T r in its original mass/length-scaled coordinates, before
    // either the constitutive right factor or numerical row whitening.
    double originalError(const std::vector<Vec>& residual, AngLin6ErrorSq& error)
    {
        double angular=0, linear=0;
        for(auto& component:m_components) component.error=0;
        for (uint32_t bond=0;bond<m_originalColumns.size();++bond)
        {
            const auto& column=m_originalColumns[bond];
            const Vec a=multiply(column.block[0],residual[column.node[0]],true);
            const Vec b=multiply(column.block[1],residual[column.node[1]],true);
            for(unsigned i=0;i<3;++i)
            {
                const double angularValue=a[i]+b[i],linearValue=a[i+3]+b[i+3];
                angular+=angularValue*angularValue; linear+=linearValue*linearValue;
                m_components[m_nodeComponent[column.node[0]]].error+=
                    angularValue*angularValue+linearValue*linearValue;
            }
        }
        error.ang=float(angular); error.lin=float(linear);
        if (!std::isfinite(angular) || !std::isfinite(linear) ||
            !std::isfinite(error.ang) || !std::isfinite(error.lin)) m_valid=false;
        return angular+linear;
    }
    bool fresh(AngLin6ErrorSq& error)
    {
        ++m_progress[2];
        if (!forward(m_s,m_x)) return false;
        for (uint32_t i=0;i<m_r.size();++i) for (unsigned j=0;j<6;++j)
            m_r[i][j]=m_rhs64[i][j]-m_s[i][j];
        const double e=originalError(m_r,error);
        m_progress[3]=m_valid && std::isfinite(e) && e<=m_limit ? 1 : 0;
        return m_progress[3]==1;
    }
    bool gradient()
    {
        m_rowWork=m_r;
        bool free=false;
        for(const auto& component:m_components) if(!component.anchored || component.qr)
        {
            free=free || (!component.anchored && !component.qr && !component.bonds.empty());
            for(uint32_t node:component.nodes) m_rowWork[node]=Vec{};
        }
        if(!whiten(m_nodeWork,m_rowWork,false) || !whiten(m_rowWork,m_nodeWork,true)
            || !transpose(m_z,m_rowWork)) return false;
        if(free)
        {
            m_rowWork=m_r;
            for(const auto& component:m_components) if(component.anchored || component.qr)
                for(uint32_t node:component.nodes) m_rowWork[node]=Vec{};
            if(!transpose(m_p,m_rowWork) || !forward(m_s,m_p)
                || !whiten(m_nodeWork,m_s,false) || !whiten(m_rowWork,m_nodeWork,true)
                || !whiten(m_nodeWork,m_rowWork,false) || !whiten(m_rowWork,m_nodeWork,true)
                || !transpose(m_p,m_rowWork)) return false;
            for(const auto& component:m_components) if(!component.anchored && !component.qr)
                for(uint32_t bond:component.bonds) m_z[bond]=m_p[bond];
        }
        if(!m_qr.empty())
        {
            if(!transpose(m_p,m_r)) return false;
            for(const auto& entry:m_qr)
            {
                const auto& component=m_components[entry.first];
                std::vector<double> value(component.bonds.size()*6);
                for(uint32_t i=0;i<component.bonds.size();++i) for(unsigned k=0;k<6;++k)
                    value[6*i+k]=m_p[component.bonds[i]][k];
                if(!entry.second.apply(value)) return m_valid=false;
                for(uint32_t i=0;i<component.bonds.size();++i) for(unsigned k=0;k<6;++k)
                    m_z[component.bonds[i]][k]=value[6*i+k];
            }
        }
        return true;
    }
    bool buildQrComponent(uint32_t id,const SolverNodeS* nodes)
    {
        if(id>=m_components.size() || m_qr.count(id)) return false;
        const auto& component=m_components[id];
        std::vector<uint32_t> offsets(m_whitening.size(),UINT32_MAX);
        uint32_t columns=0;
        for(uint32_t node:component.nodes)
        {
            if(nodes[node].mass<=0 || (!component.anchored && node==component.nodes.front())) continue;
            if(columns>UINT32_MAX-6) return false;
            offsets[node]=columns; columns+=6;
        }
        if(component.bonds.size()>UINT32_MAX/6) return false;
        const uint32_t rows=uint32_t(component.bonds.size())*6;
        if(!PrSectionQr::dimensions(rows,columns)) return false;
        std::vector<double> input(size_t(rows)*columns,0);
        for(uint32_t local=0;local<component.bonds.size();++local)
        {
            const auto& column=m_columns[component.bonds[local]];
            for(unsigned side=0;side<2;++side)
            {
                const uint32_t start=offsets[column.node[side]];
                if(start==UINT32_MAX) continue;
                for(unsigned i=0;i<6;++i) for(unsigned j=0;j<6;++j)
                    input[size_t(start+i)*rows+6*local+j]=column.block[side][6*i+j];
            }
        }
        PrSectionQr factor;
        if(!factor.build(rows,columns,std::move(input))) return false;
        m_qr.emplace(id,std::move(factor));
        return true;
    }
    bool buildAllWhitening(const SolverNodeS* nodes)
    {
        m_qr.clear();
        while(!buildWhitening(nodes))
        {
            if(!m_valid || m_failedGram==UINT32_MAX || !buildQrComponent(m_failedGram,nodes)) return m_valid=false;
        }
        for(const auto& entry:m_qr)
        { m_work[5]+=entry.second.blocks(); m_work[6]+=entry.second.bytes(); }
        return true;
    }
    // Complete SPD factor of the anchored Gram operator. Row scaling and
    // symmetric minimum-degree permutation are numerical coordinates only.
    // Detached components use a principal factor plus a six-row diagonal in
    // the RIGHT preconditioner only. Original A retains the omitted node;
    // no null mode is projected, shifted, truncated or physically anchored.
    bool whiten(std::vector<Vec>& out,const std::vector<Vec>& in,bool transposed)
    {
        out=in;
        if(!transposed)
        {
            for(uint32_t n=0;n<out.size();++n) for(unsigned k=0;k<6;++k) out[n][k]*=m_rowScale[n][k];
            for(uint32_t node:m_order)
            {
                out[node]=multiply(m_whitening[node],out[node]);
                if(!finite(out[node])) return m_valid=false;
                for(const auto& entry:m_lower[node])
                {
                    const Vec value=multiply(entry.block,out[node]);
                    for(unsigned k=0;k<6;++k) out[entry.column][k]-=value[k];
                }
            }
        }
        else
        {
            for(auto i=m_order.rbegin();i!=m_order.rend();++i)
            {
                const uint32_t node=*i;
                for(const auto& entry:m_lower[node])
                {
                    const Vec value=multiply(entry.block,out[entry.column],true);
                    for(unsigned k=0;k<6;++k) out[node][k]-=value[k];
                }
                out[node]=multiply(m_whitening[node],out[node],true);
                if(!finite(out[node])) return m_valid=false;
            }
            for(uint32_t n=0;n<out.size();++n) for(unsigned k=0;k<6;++k) out[n][k]*=m_rowScale[n][k];
        }
        for(const auto& value:out) if(!finite(value)) return m_valid=false;
        return true;
    }
    bool buildWhitening(const SolverNodeS* nodes)
    {
        m_failedGram=UINT32_MAX;
        const uint32_t count=uint32_t(m_whitening.size());
        std::vector<uint32_t> parent(count);
        std::iota(parent.begin(),parent.end(),0);
        auto root=[&](uint32_t i) { while(parent[i]!=i) { parent[i]=parent[parent[i]]; i=parent[i]; } return i; };
        for(const auto& column:m_columns)
        { const uint32_t a=root(column.node[0]),b=root(column.node[1]); if(a!=b) parent[b]=a; }
        std::map<uint32_t,uint32_t> componentIds;
        m_components.clear(); m_nodeComponent.resize(count);
        for(uint32_t n=0;n<count;++n)
        {
            const uint32_t r=root(n);
            auto inserted=componentIds.emplace(r,uint32_t(m_components.size()));
            if(inserted.second) m_components.emplace_back();
            const uint32_t id=inserted.first->second; m_nodeComponent[n]=id;
            m_components[id].nodes.push_back(n); m_components[id].qr=m_qr.count(id)!=0;
            if(nodes[n].mass<=0) m_components[id].anchored=true;
        }
        for(uint32_t b=0;b<m_columns.size();++b)
            m_components[m_nodeComponent[m_columns[b].node[0]]].bonds.push_back(b);
        std::vector<bool> eligible(count,false),diagonalOnly(count,false),eliminated(count,false);
        for(const auto& component:m_components)
            if(!component.anchored && !component.qr && !component.bonds.empty()) diagonalOnly[component.nodes.front()]=true;
        for(uint32_t n=0;n<count;++n) eligible[n]=nodes[n].mass>0 && !diagonalOnly[n] && !m_components[m_nodeComponent[n]].qr
            && !m_components[m_nodeComponent[n]].bonds.empty();
        std::vector<Mat> diagonal(count);
        std::map<std::pair<uint32_t,uint32_t>,Mat> edges;
        std::vector<std::set<uint32_t>> neighbors(count);
        for(const auto& column:m_columns)
        {
            for(unsigned side=0;side<2;++side)
            {
                const uint32_t node=column.node[side]; if(!eligible[node] && !diagonalOnly[node]) continue;
                Mat& target=diagonal[node]; const Mat& block=column.block[side];
                for(unsigned i=0;i<6;++i) for(unsigned j=0;j<=i;++j)
                {
                    double value=0; for(unsigned k=0;k<6;++k) value+=block[i*6+k]*block[j*6+k];
                    target[i*6+j]+=value; target[j*6+i]=target[i*6+j];
                }
            }
            const uint32_t a=column.node[0],b=column.node[1];
            if(a==b || !eligible[a] || !eligible[b]) continue;
            const unsigned row=a>b ? 0:1,col=1-row;
            Mat& edge=edges[{column.node[row],column.node[col]}];
            for(unsigned i=0;i<6;++i) for(unsigned j=0;j<6;++j) for(unsigned k=0;k<6;++k)
                edge[i*6+j]+=column.block[row][i*6+k]*column.block[col][j*6+k];
            neighbors[a].insert(b); neighbors[b].insert(a);
        }
        m_rowScale.assign(count,Vec{}); m_lower.assign(count,{}); m_order.clear();
        using Degree=std::pair<uint32_t,uint32_t>;
        std::priority_queue<Degree,std::vector<Degree>,std::greater<Degree>> queue;
        for(uint32_t n=0;n<count;++n)
        {
            m_whitening[n].fill(0);
            for(unsigned k=0;k<6;++k)
            {
                const double value=eligible[n] || diagonalOnly[n] ? diagonal[n][k*6+k]:1;
                if(!std::isfinite(value) || value<=0) return m_valid=false;
                m_rowScale[n][k]=1/std::sqrt(value); m_whitening[n][k*6+k]=1;
                if(!std::isfinite(m_rowScale[n][k]) || m_rowScale[n][k]<=0) return m_valid=false;
            }
            if(eligible[n])
            {
                for(unsigned i=0;i<6;++i) for(unsigned j=0;j<=i;++j)
                { diagonal[n][i*6+j]*=m_rowScale[n][i]*m_rowScale[n][j]; diagonal[n][j*6+i]=diagonal[n][i*6+j]; }
                queue.emplace(uint32_t(neighbors[n].size()),n);
            }
        }
        for(auto& entry:edges) for(unsigned i=0;i<6;++i) for(unsigned j=0;j<6;++j)
            entry.second[i*6+j]*=m_rowScale[entry.first.first][i]*m_rowScale[entry.first.second][j];
        while(!queue.empty())
        {
            const auto selected=queue.top(); queue.pop(); const uint32_t node=selected.second;
            if(eliminated[node] || selected.first!=neighbors[node].size()) continue;
            double lower[36]; if(!prSectionCholesky(diagonal[node].data(),lower))
            { m_failedGram=m_nodeComponent[node]; return false; }
            Mat& inverse=m_whitening[node]; inverse.fill(0);
            for(unsigned column=0;column<6;++column) for(unsigned row=0;row<6;++row)
            {
                double value=row==column ? 1:0;
                for(unsigned k=0;k<row;++k) value-=lower[row*6+k]*inverse[k*6+column];
                inverse[row*6+column]=value/lower[row*6+row];
                if(!std::isfinite(inverse[row*6+column])) return m_valid=false;
            }
            auto& stored=m_lower[node]; stored.reserve(neighbors[node].size());
            for(uint32_t next:neighbors[node])
            {
                const auto key=std::make_pair(std::max(node,next),std::min(node,next));
                const Mat& edge=edges.at(key); LowerEntry value{}; value.column=next;
                for(unsigned i=0;i<6;++i) for(unsigned j=0;j<6;++j) for(unsigned k=0;k<6;++k)
                    value.block[i*6+j]+=edge[next>node ? i*6+k:k*6+i]*inverse[j*6+k];
                for(double item:value.block) if(!std::isfinite(item)) return m_valid=false;
                stored.push_back(value);
            }
            for(uint32_t i=0;i<stored.size();++i)
            {
                const uint32_t a=stored[i].column; const Mat& left=stored[i].block;
                for(unsigned row=0;row<6;++row) for(unsigned col=0;col<=row;++col)
                {
                    double correction=0; for(unsigned k=0;k<6;++k) correction+=left[row*6+k]*left[col*6+k];
                    diagonal[a][row*6+col]-=correction; diagonal[a][col*6+row]=diagonal[a][row*6+col];
                }
                for(uint32_t j=0;j<i;++j)
                {
                    const uint32_t b=stored[j].column; const Mat& right=stored[j].block;
                    // Neighbor iteration is sorted, therefore a>b and this
                    // entry is the canonical lower block. Complete fill only.
                    Mat& edge=edges[{a,b}]; neighbors[a].insert(b); neighbors[b].insert(a);
                    for(unsigned row=0;row<6;++row) for(unsigned col=0;col<6;++col)
                        for(unsigned k=0;k<6;++k) edge[row*6+col]-=left[row*6+k]*right[col*6+k];
                }
            }
            for(uint32_t next:neighbors[node])
            {
                edges.erase({std::max(node,next),std::min(node,next)});
                neighbors[next].erase(node); queue.emplace(uint32_t(neighbors[next].size()),next);
            }
            neighbors[node].clear(); eliminated[node]=true; m_order.push_back(node);
        }
        m_work[0]=double(m_components.size()); m_work[1]=0;
        m_work[5]=double(m_order.size()+std::count(diagonalOnly.begin(),diagonalOnly.end(),true));
        m_work[6]=double(m_whitening.capacity()*sizeof(Mat)+m_rowScale.capacity()*sizeof(Vec)+m_order.capacity()*sizeof(uint32_t));
        for(const auto& component:m_components) if(component.anchored) ++m_work[1];
        for(const auto& column:m_lower)
        { m_work[5]+=double(column.size()); m_work[6]+=double(column.capacity()*sizeof(LowerEntry)); }
        m_work[7]=0;
        return true;
    }
    void refreshBasisBytes()
    {
        double bytes=0;
        for(const auto& component:m_components) for(const auto& basis:component.basis)
            bytes+=double((basis.direction.capacity()+basis.action.capacity())*sizeof(Vec));
        m_work[7]=bytes; m_work[10]=std::max(m_work[10],bytes);
    }

public:
    bool progress(double* output) const
    { if(!m_valid) return false; std::copy_n(m_progress,8,output); return true; }
    bool work(double* output) const
    { if(!m_valid) return false; std::copy_n(m_work,12,output); return true; }

    bool prepareSections(const SolverNodeS* nodes,uint32_t nodeCount,
        const SolverBond* bonds,uint32_t bondCount,const std::vector<PrSectionMatrix>& stiffness,
        const std::vector<double>& volumes)
    {
        m_ready=m_solution=m_pending=false;
        if(!m_valid || stiffness.size()!=bondCount) return m_valid=false;
        StressProcessor::DataParams parameters;
        parameters.centerBonds=parameters.equalizeMasses=false;
        StressProcessor::prepare(nodes,nodeCount,bonds,bondCount,parameters);
        if(volumes.size()!=nodeCount) return m_valid=false;
        double logMass=0,lengthSum=0; uint64_t massCount=0,lengthCount=0;
        for(uint32_t n=0;n<nodeCount;++n) if(nodes[n].mass>0)
        { logMass+=std::log(double(nodes[n].mass)); ++massCount; }
        for(uint32_t b=0;b<bondCount;++b) for(unsigned side=0;side<2;++side)
        {
            const uint32_t n=bonds[b].nodes[side];
            if(nodes[n].mass<=0) continue;
            const double x=double(bonds[b].centroid.x)-nodes[n].CoM.x;
            const double y=double(bonds[b].centroid.y)-nodes[n].CoM.y;
            const double z=double(bonds[b].centroid.z)-nodes[n].CoM.z;
            lengthSum+=std::sqrt(x*x+y*y+z*z); ++lengthCount;
        }
        m_preciseMass=massCount ? std::exp(logMass/double(massCount)):1;
        m_preciseLength=lengthCount ? lengthSum/double(lengthCount):1;
        const double linearScale=m_preciseMass*m_preciseLength;
        const double angularScale=linearScale*m_preciseLength;
        m_physicalWeights.assign(nodeCount,Vec{});
        for(uint32_t n=0;n<nodeCount;++n)
        {
            if(nodes[n].mass<=0) continue;
            const double mass=nodes[n].mass,volume=volumes[n];
            if(!std::isfinite(volume) || volume<=0) return m_valid=false;
            const double radius=std::cbrt(volume*3/(4*std::acos(-1.)));
            const double inertia=.4*mass*radius*radius;
            const double angular=1/(m_preciseLength*std::sqrt(m_preciseMass*inertia));
            const double linear=1/(m_preciseLength*std::sqrt(m_preciseMass*mass));
            if(!std::isfinite(inertia) || inertia<=0 || !std::isfinite(angular) || angular<=0
                || !std::isfinite(linear) || linear<=0) return m_valid=false;
            for(unsigned axis=0;axis<3;++axis)
            { m_physicalWeights[n][axis]=angular; m_physicalWeights[n][axis+3]=linear; }
        }
        if(!std::isfinite(linearScale) || !std::isfinite(angularScale) || linearScale<=0 || angularScale<=0)
            return m_valid=false;
        m_factor.resize(bondCount); m_columns.resize(bondCount); m_originalColumns.resize(bondCount); m_whitening.resize(nodeCount);
        double common=0;
        for(uint32_t b=0;b<bondCount;++b)
        {
            double lower[36];
            if(!prSectionCholesky(stiffness[b].data(),lower)) return m_valid=false;
            for(unsigned i=0;i<6;++i) for(unsigned j=0;j<6;++j)
            {
                const double value=i<3 ? -lower[(i+3)*6+j]/angularScale : lower[(i-3)*6+j]/linearScale;
                if(!std::isfinite(value)) return m_valid=false;
                m_factor[b][i*6+j]=value;
                common=std::max(common,std::abs(value));
            }
        }
        if(!bondCount) common=1;
        if(!std::isfinite(common) || common<=0) return m_valid=false;
        for(uint32_t b=0;b<bondCount;++b)
        {
            for(unsigned i=0;i<36;++i)
            {
                double& value=m_factor[b][i]; value/=common;
                const float representation=float(value);
                // Retain the existing explicit float representability domain:
                // every physical Cholesky diagonal mode must survive, even
                // though revision 3 stores and computes accepted factors in f64.
                if(!std::isfinite(value) || !std::isfinite(representation) ||
                    ((i/6+3)%6==i%6 && representation==0)) return m_valid=false;
            }
            Column& column=m_columns[b]; Column& original=m_originalColumns[b];
            column.node[0]=original.node[0]=bonds[b].nodes[0];
            column.node[1]=original.node[1]=bonds[b].nodes[1];
            for(unsigned side=0;side<2;++side)
            {
                const uint32_t node=column.node[side];
                const double r[3]={
                    (double(bonds[b].centroid.x)-nodes[node].CoM.x)/m_preciseLength,
                    (double(bonds[b].centroid.y)-nodes[node].CoM.y)/m_preciseLength,
                    (double(bonds[b].centroid.z)-nodes[node].CoM.z)/m_preciseLength};
                const double angular=m_physicalWeights[node][0]*angularScale;
                const double linear=m_physicalWeights[node][3]*linearScale;
                const double sign=side ? -1:1;
                original.block[side].fill(0); column.block[side].fill(0);
                for(unsigned i=0;i<3;++i) for(unsigned j=0;j<6;++j)
                {
                    const unsigned a=(i+1)%3,c=(i+2)%3;
                    original.block[side][i*6+j]=sign*angular*((i==j ? 1.:0.)
                        -r[a]*(c+3==j ? 1.:0.)+r[c]*(a+3==j ? 1.:0.));
                    original.block[side][(i+3)*6+j]=sign*linear*(i+3==j ? 1.:0.);
                }
                for(unsigned i=0;i<6;++i) for(unsigned j=0;j<6;++j) for(unsigned k=0;k<6;++k)
                    column.block[side][i*6+j]+=original.block[side][i*6+k]*m_factor[b][k*6+j];
                for(double value:column.block[side]) if(!std::isfinite(value)) return m_valid=false;
            }
        }
        if(!buildAllWhitening(nodes)) return false;
        m_x.assign(bondCount,Vec{}); m_p.resize(bondCount); m_z.resize(bondCount);
        m_rhs64.resize(nodeCount); m_r.resize(nodeCount); m_s.resize(nodeCount); m_nodeWork.resize(nodeCount); m_rowWork.resize(nodeCount);
        m_ready=true;
        return true;
    }

    bool solveSections(AngLin6* output,const std::vector<std::array<double,3>>& forces,uint32_t iterations,
        float tolerance,bool changed,AngLin6ErrorSq& error)
    {
        auto fail=[&]() { m_valid=false; m_pending=false;
            error.ang=error.lin=std::numeric_limits<float>::quiet_NaN(); return false; };
        if(!m_valid || !m_ready || !iterations) return fail();
        if(forces.size()!=getNodeCount()) return fail();
        for(uint32_t i=0;i<getNodeCount();++i)
        {
            m_rhs64[i]=Vec{};
            for(unsigned axis=0;axis<3;++axis)
                m_rhs64[i][axis+3]=-m_physicalWeights[i][axis+3]*forces[i][axis];
            if(!finite(m_rhs64[i])) return fail();
        }
        const bool resumed=m_solution && m_pending && !changed;
        m_progress[0]=m_progress[2]=m_progress[3]=0; m_progress[1]=resumed ? 1:0;
        m_work[2]=m_work[8]=0;
        if(!resumed)
        {
            const double rhsNormSquared=normSquared(m_rhs64);
            m_limit=double(tolerance)*tolerance*rhsNormSquared;
            if(!std::isfinite(m_limit) || m_limit<0) return fail();
            for(auto& component:m_components)
            {
                double square=0,correction=0;
                for(uint32_t node:component.nodes) for(double value:m_rhs64[node])
                {
                    const double term=value*value-correction,next=square+term;
                    correction=(next-square)-term; square=next;
                }
                component.limit=double(tolerance)*tolerance*square;
                if(!std::isfinite(component.limit) || component.limit<0) return fail();
                component.updates=0; component.basis.clear();
                // Exact unloaded-component minimizer, including a partial
                // unload while another disconnected component remains loaded.
                if(square==0) for(uint32_t bond:component.bonds) m_x[bond]=Vec{};
            }
            refreshBasisBytes(); m_work[4]=0;
            if(!forward(m_s,m_x)) return fail();
            for(uint32_t i=0;i<m_r.size();++i) for(unsigned j=0;j<6;++j)
                m_r[i][j]=m_rhs64[i][j]-m_s[i][j];
        }
        bool converged=false;
        while(m_progress[0]<iterations)
        {
            const double e=originalError(m_r,error);
            if(!m_valid || !std::isfinite(e)) return fail();
            if(e<=m_limit)
            {
                if(fresh(error)) { converged=true; break; }
                if(!m_valid) return fail();
                // Recomputed residual exposes only arithmetic drift. Restart
                // free directions, retain their range-valid private solution.
                for(auto& component:m_components) component.basis.clear();
                refreshBasisBytes();
            }
            bool active=false;
            for(auto& component:m_components)
            {
                component.active=component.error>component.limit && !component.bonds.empty();
                active=active || component.active;
            }
            // Component limits sum to the global bound mathematically. If
            // summation rounding separates them, continue all nonzero errors;
            // only the fresh original global test can authorize convergence.
            if(!active) for(auto& component:m_components)
                component.active=component.error>0 && !component.bonds.empty();
            if(!gradient()) return fail();
            m_p=m_z;
            for(const auto& component:m_components) if(!component.active)
                for(uint32_t bond:component.bonds) m_p[bond]=Vec{};
            if(!forward(m_s,m_p)) return fail();
            for(auto& component:m_components) if(component.active && (!component.anchored || component.qr))
            {
                // Two-pass modified Gram-Schmidt in original A-action space.
                // All directions remain in range(A^T). There is no singular
                // value cutoff, gauge projection, material or row-norm change.
                for(unsigned pass=0;pass<2;++pass) for(const auto& basis:component.basis)
                {
                    double coefficient=0;
                    for(uint32_t i=0;i<component.nodes.size();++i)
                        for(unsigned k=0;k<6;++k) coefficient+=m_s[component.nodes[i]][k]*basis.action[i][k];
                    if(!std::isfinite(coefficient)) return fail();
                    for(uint32_t i=0;i<component.bonds.size();++i) for(unsigned k=0;k<6;++k)
                        m_p[component.bonds[i]][k]-=coefficient*basis.direction[i][k];
                    for(uint32_t i=0;i<component.nodes.size();++i) for(unsigned k=0;k<6;++k)
                        m_s[component.nodes[i]][k]-=coefficient*basis.action[i][k];
                    ++m_work[8];
                }
            }
            if(!whiten(m_nodeWork,m_s,false) || !whiten(m_rowWork,m_r,false)) return fail();
            active=false;
            for(auto& component:m_components) if(component.active)
            {
                active=true; double denominator=0,numerator=0;
                for(uint32_t node:component.nodes) for(unsigned k=0;k<6;++k)
                {
                    const double action=component.anchored ? m_nodeWork[node][k]:m_s[node][k];
                    const double residual=component.anchored ? m_rowWork[node][k]:m_r[node][k];
                    denominator+=action*action;
                    numerator+=residual*action;
                }
                if(!std::isfinite(denominator) || denominator<=0 || !std::isfinite(numerator)) return fail();
                const double length=std::sqrt(denominator),alpha=numerator/length;
                if(!std::isfinite(alpha)) return fail();
                for(uint32_t bond:component.bonds) for(unsigned k=0;k<6;++k)
                {
                    m_p[bond][k]/=length; m_x[bond][k]+=alpha*m_p[bond][k];
                }
                for(uint32_t node:component.nodes) for(unsigned k=0;k<6;++k)
                { m_s[node][k]/=length; m_r[node][k]-=alpha*m_s[node][k]; }
                if(!component.anchored || component.qr)
                {
                    Basis basis; basis.direction.reserve(component.bonds.size()); basis.action.reserve(component.nodes.size());
                    for(uint32_t bond:component.bonds) basis.direction.push_back(m_p[bond]);
                    for(uint32_t node:component.nodes) basis.action.push_back(m_s[node]);
                    component.basis.push_back(std::move(basis));
                }
                ++component.updates; ++m_work[2];
                m_work[4]=std::max(m_work[4],double(component.updates));
            }
            if(!active) return fail();
            refreshBasisBytes(); ++m_progress[0];
        }
        if(!converged)
        {
            const double e=originalError(m_r,error);
            if(!m_valid || !std::isfinite(e)) return fail();
            if(e<=m_limit)
            {
                converged=fresh(error);
                if(!m_valid) return fail();
                if(!converged)
                { for(auto& component:m_components) component.basis.clear(); refreshBasisBytes(); }
            }
        }
        if(m_work[3]>9007199254740991.0-m_work[2] || m_work[9]>9007199254740991.0-m_work[8]) return fail();
        m_work[3]+=m_work[2]; m_work[9]+=m_work[8];
        m_solution=true; m_pending=!converged;
        const double increments[3]={m_progress[0],m_progress[1],m_progress[2]};
        for(unsigned i=0;i<3;++i)
        {
            if(m_progress[4+i]>9007199254740991.0-increments[i]) return fail();
            m_progress[4+i]+=increments[i];
        }
        const double linearScale=m_preciseLength*m_preciseMass;
        const double angularScale=m_preciseLength*linearScale;
        for(uint32_t b=0;b<getBondCount();++b)
        {
            const Vec value=multiply(m_factor[b],m_x[b]);
            for(unsigned i=0;i<6;++i)
            {
                const float nativeValue=float(value[i]*(i<3 ? angularScale : linearScale));
                if(!std::isfinite(nativeValue)) return fail();
                prSectionSet(output[b],i,nativeValue);
            }
        }
        return converged;
    }
};
